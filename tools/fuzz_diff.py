#!/usr/bin/env python3
"""Differential fuzzer: random programs through the interpreter and the translator.

Generates a straight-line program from the supported instruction set with random
operands, then runs it both ways and compares registers, condition code and
storage. Any mismatch is a bug in one of the two paths.

    python3 tools/fuzz_diff.py --iterations 200 --seed 1

Exit status is non-zero if any case disagrees. Failing programs are written to
_fuzz_failures/ so they can be replayed.
"""

from __future__ import annotations

import argparse
import pathlib
import random
import shutil
import subprocess
import sys
import tempfile
import zlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from titor370.asm import assemble  # noqa: E402
from titor370.cpu import CPU, MEMSIZE, S370Error  # noqa: E402
from titor370.translate import translate  # noqa: E402

CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")

# Instructions that are safe to generate randomly: no branches, no privileged
# operations, no instructions whose operands must be consistent (SS lengths).
RR = ["LR", "LTR", "LNR", "LPR", "LCR", "NR", "OR", "XR", "AR", "SR", "ALR", "SLR",
      "CR", "CLR", "MR", "DR"]
# Divides raise on zero/overflow, which skips the case; keep them rarer.
DIVIDES = {"MR", "DR", "D", "M"}
RX = ["L", "ST", "LA", "LH", "STH", "IC", "STC", "A", "S", "AL", "SL", "N", "O",
      "X", "C", "CL", "M", "D", "AH", "SH", "CH", "MH", "CVD", "CVB"]
RS = ["SLL", "SRL", "SRA", "SLA", "SLDL", "SRDL", "SLDA", "SRDA", "STM", "LM", "CS"]
SI = ["MVI", "NI", "OI", "XI"]
SS = ["MVC", "MVN", "MVZ", "NC", "OC", "XC", "CLC", "PACK", "UNPK"]


def random_program(rng: random.Random, origin: int = 0x1000) -> str:
    lines = ["         ORG   0x1000", "START    BALR  12,0", "         USING *,12"]
    lines.append("         LA    13,DATA")
    count = rng.randrange(8, 20)
    for _ in range(count):
        kind = rng.choices(["RR", "RX", "RS", "SI", "SS"], weights=[4, 6, 2, 2, 2])[0]
        if kind == "RR":
            name = rng.choice([x for x in RR if x not in DIVIDES] if rng.random() < 0.8 else RR)
            r1 = rng.choice([0, 2, 4, 6, 8])       # even for MR/DR pairs
            r2 = rng.randrange(16)
            if name in ("MR", "DR"):
                lines.append(f"         {name:<5} {r1},{r2}")
            else:
                lines.append(f"         {name:<5} {r1},{r2}")
        elif kind == "RX":
            name = rng.choice([x for x in RX if x not in DIVIDES] if rng.random() < 0.9 else RX)
            r1 = rng.choice([0, 2, 4, 6, 8])
            x = rng.choice([0, 0, 0, 13])           # usually no index
            d = rng.randrange(0, 0x40) * 4
            lines.append(f"         {name:<5} {r1},{d}({x},13)")
        elif kind == "RS":
            name = rng.choice(RS)
            r1 = rng.choice([0, 2, 4, 6, 8])
            if name in ("SLL", "SRL", "SRA", "SLA", "SLDL", "SRDL", "SLDA", "SRDA"):
                lines.append(f"         {name:<5} {r1},{rng.randrange(0, 64)}")
            elif name == "CS":
                lines.append(f"         {name:<5} {r1},{r1 + 2},{rng.randrange(0, 0x20) * 4}(13)")
            else:
                lines.append(f"         {name:<5} {r1},{r1 + 2},{rng.randrange(0, 0x20) * 4}(13)")
        elif kind == "SI":
            name = rng.choice(SI)
            lines.append(f"         {name:<5} X'{rng.randrange(256):02X}',{rng.randrange(0, 0x20) * 4}(13)")
        else:
            name = rng.choice(SS)
            n = rng.randrange(1, 9)
            a = rng.randrange(0, 0x10) * 4
            b = rng.randrange(0, 0x10) * 4
            lines.append(f"         {name:<5} {a}({n},13),{b}({n},13)")
    lines.append("         DIAG  0,0,0")
    lines.append("DATA     DC    64X'00'")
    lines.append("         END   START")
    return "\n".join(lines) + "\n"


def initial_state(source: str, result: dict) -> tuple[list[int], bytes]:
    """Registers and initial data, identical for both execution paths."""
    rng = random.Random(zlib.crc32(source.encode()))
    regs = [rng.randrange(0, 1 << 32) for _ in range(16)]
    regs[12] = 0
    regs[13] = result["symbols"]["DATA"]
    blob = bytes(rng.randrange(256) for _ in range(256))
    return regs, blob


def run_case(source: str, workdir: pathlib.Path) -> tuple[bool, str]:
    result = assemble(source, origin=0x1000)
    window_len = 4096
    regs, blob = initial_state(source, result)
    data_addr = result["symbols"]["DATA"]

    # Interpreter
    cpu = CPU()
    cpu.r[:] = regs
    cpu.load(result["base"], result["image"])
    cpu.write(data_addr, blob)
    try:
        cpu.run(result["entry"], max_steps=500)
    except S370Error as exc:
        return True, f"interpreter raised ({exc}); skipped"

    # Translated C
    store = bytearray(MEMSIZE)
    store[result["base"] : result["base"] + len(result["image"])] = result["image"]
    store[data_addr : data_addr + 256] = blob
    # The emitted image must cover the seeded data as well as the program, or
    # the C program would start with zeros where the interpreter has data.
    image_len = max(len(result["image"]), data_addr + 256 - result["base"])
    source_c = translate(
        store,
        base=result["base"],
        entry=result["entry"],
        length=image_len,
        window_base=result["base"],
        window_len=window_len,
        registers=regs,
    )

    cpath = workdir / "case.c"
    exe = workdir / "case"
    cpath.write_text(source_c)
    build = subprocess.run([CC, "-O1", "-std=c11", "-o", str(exe), str(cpath)],
                           capture_output=True, text=True)
    if build.returncode != 0:
        return False, f"compile failed\n{build.stderr}"
    run = subprocess.run([str(exe)], capture_output=True, text=True)
    if run.returncode != 0:
        # The interpreter completed, so the C bailing out is a divergence.
        return False, f"translated program exited {run.returncode}: {run.stderr.strip()}"

    parsed = {}
    for line in run.stdout.splitlines():
        key, _, value = line.partition(" ")
        parsed[key] = value
    regs = [int(v, 16) for v in parsed["R"].split()]
    if regs != list(cpu.r):
        return False, f"registers differ\n  interpreter {[f'{v:08X}' for v in cpu.r]}\n  translated  {[f'{v:08X}' for v in regs]}"
    if int(parsed["CC"]) != cpu.cc:
        return False, f"CC differs: interpreter {cpu.cc}, translated {parsed['CC']}"
    mem = bytes.fromhex(parsed["MEM"])
    want = bytes(cpu.memory[result["base"] : result["base"] + window_len])
    if mem != want:
        for i in range(window_len):
            if mem[i] != want[i]:
                return False, f"storage differs at {result['base'] + i:06X}: interpreter {want[i]:02X}, translated {mem[i]:02X}"
    return True, "match"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--iterations", type=int, default=100)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--keep", action="store_true", help="keep mismatching programs")
    args = ap.parse_args()

    if CC is None:
        print("no C compiler found", file=sys.stderr)
        return 2

    rng = random.Random(args.seed)
    failures = 0
    skipped = 0
    with tempfile.TemporaryDirectory() as tmp:
        workdir = pathlib.Path(tmp)
        outdir = ROOT / "_fuzz_failures"
        for index in range(args.iterations):
            source = random_program(rng)
            ok, detail = run_case(source, workdir)
            if detail.startswith("interpreter raised"):
                skipped += 1
                continue
            if not ok:
                failures += 1
                print(f"case {index}: MISMATCH\n{detail}\n")
                if args.keep:
                    outdir.mkdir(exist_ok=True)
                    (outdir / f"case-{args.seed}-{index}.s370").write_text(source)
                if failures >= 5:
                    print("stopping after 5 failures")
                    break
            elif index % 25 == 0:
                print(f"case {index}: ok")

    print(f"\n{args.iterations} programs: {failures} mismatches, {skipped} skipped (program check or translated exit)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
