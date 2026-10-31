"""Equivalence: the interpreter and the C translation must agree.

For every example program, this test runs the machine code twice -- once on the
Python interpreter, once through the C that translate.py generates -- and
compares all sixteen registers, the condition code, and the program's storage.

That is the claim the 5100 could never make: not "it runs mainframe code", but
"this machine code and this C program are the same program, and here is the
evidence".
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import tempfile

import pytest

from titor370.asm import assemble
from titor370.cpu import CPU, MEMSIZE
from titor370.translate import translate

EXAMPLES = sorted((pathlib.Path(__file__).resolve().parent.parent / "examples").glob("*.s370"))
CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")

pytestmark = pytest.mark.skipif(CC is None, reason="no C compiler available")


def _store(result: dict) -> bytearray:
    store = bytearray(MEMSIZE)
    store[result["base"] : result["base"] + len(result["image"])] = result["image"]
    return store


def _c_program(result: dict, window: int) -> str:
    return translate(
        _store(result),
        base=result["base"],
        entry=result["entry"],
        length=len(result["image"]),
        window_base=result["base"],
        window_len=window,
    )


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_translation_matches_interpreter(path):
    result = assemble(path.read_text())
    window = result["end"] - result["base"] + 64

    cpu = CPU()
    cpu.load(result["base"], result["image"])
    cpu.run(result["entry"])
    reference_regs = list(cpu.r)
    reference_cc = cpu.cc
    reference_mem = bytes(cpu.memory[result["base"] : result["base"] + window])

    source = _c_program(result, window)
    with tempfile.TemporaryDirectory() as tmp:
        cpath = pathlib.Path(tmp) / "t.c"
        exe = pathlib.Path(tmp) / "t"
        cpath.write_text(source)
        build = subprocess.run(
            [CC, "-O1", "-std=c11", "-o", str(exe), str(cpath)],
            capture_output=True,
            text=True,
        )
        assert build.returncode == 0, f"compile failed:\n{build.stderr}"
        run = subprocess.run([str(exe)], capture_output=True, text=True)
        assert run.returncode == 0, f"translated program failed: {run.stderr}"

    parsed = {}
    for line in run.stdout.splitlines():
        key, _, value = line.partition(" ")
        parsed[key] = value

    translated_regs = [int(v, 16) for v in parsed["R"].split()]
    translated_cc = int(parsed["CC"])
    translated_mem = bytes.fromhex(parsed["MEM"])

    assert translated_regs == reference_regs
    assert translated_cc == reference_cc
    assert translated_mem == reference_mem


def test_translation_is_deterministic():
    result = assemble((pathlib.Path(__file__).resolve().parent.parent / "examples" / "sum.s370").read_text())
    first = _c_program(result, 32)
    second = _c_program(result, 32)
    assert first == second
