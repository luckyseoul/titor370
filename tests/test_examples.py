"""End-to-end tests: assemble a program, run it, and check an independently
computed answer. The expected values come from Python's own arithmetic, not
from the emulator, so these are real checks rather than golden files.
"""

from __future__ import annotations

import math
import pathlib

import pytest

from titor370.asm import assemble
from titor370.cpu import CPU

EXAMPLES = pathlib.Path(__file__).resolve().parent.parent / "examples"


def run_example(name: str) -> tuple[CPU, dict]:
    result = assemble((EXAMPLES / name).read_text())
    cpu = CPU()
    cpu.load(result["base"], result["image"])
    cpu.run(result["entry"])
    return cpu, result


def test_sum_program_totals_the_table():
    cpu, result = run_example("sum.s370")
    expected = sum([10, 20, 30, 40, 50])
    assert expected == 150
    assert cpu.r[1] == expected
    assert cpu.fetch_u32(result["symbols"]["RESULT"]) == expected
    assert cpu.cc == 2


def test_factorial_program():
    cpu, result = run_example("factorial.s370")
    expected = math.factorial(10)
    assert expected == 3_628_800
    assert cpu.r[1] == expected
    assert cpu.fetch_u32(result["symbols"]["RESULT"]) == expected


def test_strings_program_copies_searches_and_translates():
    cpu, result = run_example("strings.s370")
    copy = result["symbols"]["COPY"]
    got = cpu.read(copy, 11)
    expected = "HELLO WORLD".encode("cp037")
    assert got == expected

    # TRT found lower-case 'o' at offset 4 and reported tag 1.
    assert cpu.r[1] == 1
    assert cpu.r[2] == copy + 4
    assert cpu.cc == 2


def test_privileged_instruction_is_reported():
    from titor370.cpu import PrivilegedOperation

    source = """
             ORG   0x1000
             SSM   0             ; privileged: a problem-state program may not
    """
    result = assemble(source)
    cpu = CPU()
    cpu.load(result["base"], result["image"])
    with pytest.raises(PrivilegedOperation):
        cpu.run(result["entry"], max_steps=2)


def test_search_program_finds_the_value_with_a_subroutine():
    cpu, result = run_example("search.s370")
    # The table is 10, 30, 50, 70, 90, 90; the needle is 70, the fourth word.
    table = [10, 30, 50, 70, 90, 90]
    needle = 70
    expected = needle
    assert cpu.r[2] == expected
    assert cpu.fetch_u32(result["symbols"]["RESULT"]) == expected
    assert cpu.r[1] == table.index(needle) * 4


def test_shifts_example():
    cpu, result = run_example("shifts.s370")
    # 1 << 32 shifts everything out; 1 >> 40 likewise; -8 >> 33 is all sign bits.
    assert cpu.fetch_u32(result["symbols"]["OUT1"]) == 0
    assert cpu.fetch_u32(result["symbols"]["OUT2"]) == 0
    assert cpu.fetch_u32(result["symbols"]["OUT3"]) == 0xFFFFFFFF


def test_icm_example_uses_the_leftmost_inserted_byte():
    cpu, _ = run_example("icm.s370")
    assert cpu.cc == 2
