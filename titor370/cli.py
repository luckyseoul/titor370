"""Command line interface for titor370."""

from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile

from .asm import AsmError, assemble
from .cpu import CPU, S370Error
from .disasm import disassemble, listing as make_listing
from .translate import TranslateError, translate


def _store(result: dict):
    """A full 16 MiB store with the program image placed at its base."""
    from .cpu import MEMSIZE

    store = bytearray(MEMSIZE)
    store[result["base"] : result["base"] + len(result["image"])] = result["image"]
    return store


def _load_source(path: str, origin: int) -> dict:
    text = pathlib.Path(path).read_text()
    return assemble(text, origin=origin)


def cmd_asm(args) -> int:
    result = _load_source(args.source, args.origin)
    print("\n".join(result["listing"]))
    print()
    print(f"base  {result['base']:06X}")
    print(f"entry {result['entry']:06X}")
    print(f"size  {len(result['image'])} bytes")
    print()
    for offset in range(0, len(result["image"]), 16):
        chunk = result["image"][offset : offset + 16]
        print(f"{result['base'] + offset:06X}  " + " ".join(f"{b:02X}" for b in chunk))
    return 0


def cmd_dis(args) -> int:
    data = pathlib.Path(args.binary).read_bytes()
    rows = disassemble(data, 0, len(data))
    print(make_listing(rows))
    return 0


def cmd_run(args) -> int:
    result = _load_source(args.source, args.origin)
    cpu = CPU()
    cpu.load(result["base"], result["image"])
    cpu.r[15] = result["entry"]
    cpu.trace = [] if args.trace else None
    try:
        cpu.run(result["entry"], max_steps=args.max_steps)
    except S370Error as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        return 2
    if args.trace and cpu.trace:
        print("\n".join(cpu.trace))
        print()
    print("registers")
    for i in range(0, 16, 4):
        print("  " + "  ".join(f"R{j:<2} {cpu.r[j]:08X}" for j in range(i, i + 4)))
    print(f"CC {cpu.cc}   IA {cpu.psw.ia:06X}   steps {cpu.steps}")
    if args.memory:
        base, _, length = args.memory.partition(":")
        start = int(base, 16)
        n = int(length) if length else 32
        print(f"memory {start:06X}..{start + n:06X}")
        data = cpu.read(start, n)
        for offset in range(0, n, 16):
            chunk = data[offset : offset + 16]
            print(f"  {start + offset:06X}  " + " ".join(f"{b:02X}" for b in chunk))
    print("executed")
    for name, count in sorted(cpu.instructions.items(), key=lambda kv: -kv[1]):
        print(f"  {name:<6} {count}")
    return 0


def cmd_translate(args) -> int:
    result = _load_source(args.source, args.origin)
    window_base = int(args.window.split(":")[0], 16) if args.window else result["base"]
    window_len = int(args.window.split(":")[1]) if args.window else 64
    try:
        c_source = translate(
            _store(result),
            base=result["base"],
            entry=result["entry"],
            length=len(result["image"]),
            window_base=window_base,
            window_len=window_len,
            title=f"translated from {args.source}",
        )
    except TranslateError as exc:
        print(f"translate error: {exc}", file=sys.stderr)
        return 2
    if args.output:
        pathlib.Path(args.output).write_text(c_source)
        print(f"wrote {args.output} ({len(c_source)} bytes of C)")
    else:
        sys.stdout.write(c_source)
    return 0


def _run_in_emulator(result: dict, max_steps: int) -> dict:
    cpu = CPU()
    cpu.load(result["base"], result["image"])
    cpu.run(result["entry"], max_steps=max_steps)
    return cpu.snapshot() | {"memory": bytes(cpu.memory)}


def cmd_compare(args) -> int:
    """Assemble, run on the interpreter, translate to C, compile, run, compare."""
    result = _load_source(args.source, args.origin)
    window_base = result["base"]
    # Default: compare the whole loaded program plus a margin for DS-reserved data.
    window_len = args.window or (result["end"] - result["base"] + 64)

    from .cpu import IMPLEMENTED  # noqa: F401  (documents the dependency)
    from .isa import OPCODES

    used = {w for w in pathlib.Path(args.source).read_text().split() if w in OPCODES}
    untranslatable = used & {"AP", "SP", "MP", "DP", "ZAP", "CP", "MVCL", "CLCL",
                             "EX", "CDS", "SVC", "SSM", "LPSW", "LRA", "ED", "EDMK",
                             "SRP", "MVO"}
    if untranslatable:
        print(f"SKIP  uses {', '.join(sorted(untranslatable))}, which the translator does not emit")
        return 0

    try:
        reference = _run_in_emulator(result, args.max_steps)
    except S370Error as exc:
        print(f"emulator stopped: {exc}", file=sys.stderr)
        return 2

    c_source = translate(
        _store(result),
        base=result["base"],
        entry=result["entry"],
        length=len(result["image"]),
        window_base=window_base,
        window_len=window_len,
        title=f"translated from {args.source}",
    )

    cc = shutil.which(args.cc)
    if not cc:
        print(f"no C compiler found ({args.cc})", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        cpath = pathlib.Path(tmp) / "translated.c"
        exe = pathlib.Path(tmp) / "translated"
        cpath.write_text(c_source)
        build = subprocess.run(
            [cc, "-O1", "-std=c11", "-o", str(exe), str(cpath)],
            capture_output=True,
            text=True,
        )
        if build.returncode != 0:
            print("compile failed:", file=sys.stderr)
            print(build.stderr, file=sys.stderr)
            return 2
        run = subprocess.run([str(exe)], capture_output=True, text=True)
        if run.returncode != 0:
            print(f"translated program exited {run.returncode}: {run.stderr}", file=sys.stderr)
            return 2

    translated = {}
    for line in run.stdout.splitlines():
        key, _, value = line.partition(" ")
        translated[key] = value

    regs = [int(v, 16) for v in translated["R"].split()]
    failures = []

    for i, (a, b) in enumerate(zip(reference["r"], regs)):
        if a != b:
            failures.append(f"R{i}: interpreter {a:08X} != translated {b:08X}")
    if reference["cc"] != int(translated["CC"]):
        failures.append(f"CC: interpreter {reference['cc']} != translated {translated['CC']}")
    mem = bytes.fromhex(translated["MEM"])
    want = reference["memory"][window_base : window_base + window_len]
    if mem != want:
        failures.append(f"memory window differs:\n  interpreter {want.hex().upper()}\n  translated  {mem.hex().upper()}")

    if failures:
        print("MISMATCH")
        for f in failures:
            print("  " + f)
        return 1

    print("MATCH")
    print(f"  {len(result['image'])} bytes of S/370, {reference['steps']} instructions")
    print(f"  registers, condition code and {window_len} bytes of storage agree")
    print("  final registers: " + " ".join(f"{v:08X}" for v in regs))
    print(f"  CC {reference['cc']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="titor370", description=__doc__)
    parser.add_argument("--origin", type=lambda s: int(s, 0), default=0x1000)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("asm", help="assemble and show the listing and object bytes")
    p.add_argument("source")
    p.set_defaults(func=cmd_asm)

    p = sub.add_parser("dis", help="disassemble a flat binary")
    p.add_argument("binary")
    p.set_defaults(func=cmd_dis)

    p = sub.add_parser("run", help="assemble and execute")
    p.add_argument("source")
    p.add_argument("--trace", action="store_true")
    p.add_argument("--max-steps", type=int, default=1_000_000)
    p.add_argument("--memory", help="hex start[:length] to dump")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("translate", help="translate to C")
    p.add_argument("source")
    p.add_argument("-o", "--output")
    p.add_argument("--window", help="hex BASE:LENGTH of storage to export")
    p.set_defaults(func=cmd_translate)

    p = sub.add_parser("compare", help="prove the C translation matches the interpreter")
    p.add_argument("source")
    p.add_argument("--window", type=int, default=0, help="bytes of storage to compare")
    p.add_argument("--max-steps", type=int, default=1_000_000)
    p.add_argument("--cc", default="cc")
    p.set_defaults(func=cmd_compare)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (AsmError, S370Error, TranslateError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
