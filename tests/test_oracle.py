"""Oracle tests: compare the CPU against an independent computation.

These do not use golden values. Each expected result is computed here, in
Python, from the operation's definition, so a bug in the emulator cannot hide
by also being in the expectation.
"""

from __future__ import annotations

import random

import pytest

from titor370.cpu import CPU, s32, u32
from titor370.isa import encode

EDGES = [
    0,
    1,
    2,
    0x7FFFFFFF,          # max positive
    0x80000000,          # max negative
    0x80000001,
    0xFFFFFFFF,          # -1
    0xFFFFFFFE,
    0x0000FFFF,
    0x7FFFFFFE,
]


def execute(name, a, b):
    cpu = CPU()
    cpu.load(0x1000, encode(name, 1, 2))
    cpu.r[1] = a
    cpu.r[2] = b
    cpu.psw.ia = 0x1000
    cpu.step()
    return cpu.r[1], cpu.cc


def signed_cc(value):
    if value == 0:
        return 0
    return 1 if value < 0 else 2


@pytest.mark.parametrize("a", EDGES)
@pytest.mark.parametrize("b", EDGES)
def test_add_and_subtract_match_python(a, b):
    result, cc = execute("AR", a, b)
    exact = s32(a) + s32(b)
    if -2**31 <= exact < 2**31:
        assert (result, cc) == (u32(exact), signed_cc(exact))
    else:
        assert cc == 3 and result == u32(exact)

    result, cc = execute("SR", a, b)
    exact = s32(a) - s32(b)
    if -2**31 <= exact < 2**31:
        assert (result, cc) == (u32(exact), signed_cc(exact))
    else:
        assert cc == 3 and result == u32(exact)


@pytest.mark.parametrize("a", EDGES)
@pytest.mark.parametrize("b", EDGES)
def test_logical_add_and_subtract_match_python(a, b):
    result, cc = execute("ALR", a, b)
    exact = a + b
    carry = exact > 0xFFFFFFFF
    assert result == exact & 0xFFFFFFFF
    assert cc == (2 if carry else 0) + (1 if result else 0)

    result, cc = execute("SLR", a, b)
    # Carry out of the sign position: a >= b. CC 1 nonzero/no carry,
    # 2 zero/carry, 3 nonzero/carry; CC 0 is unreachable.
    carry = a >= b
    assert result == (a - b) & 0xFFFFFFFF
    assert cc == (2 if carry else 0) + (1 if result else 0)


@pytest.mark.parametrize("a", EDGES)
@pytest.mark.parametrize("b", EDGES)
def test_compare_matches_python(a, b):
    _, cc = execute("CR", a, b)
    assert cc == (0 if s32(a) == s32(b) else (1 if s32(a) < s32(b) else 2))
    _, cc = execute("CLR", a, b)
    assert cc == (0 if a == b else (1 if a < b else 2))


@pytest.mark.parametrize("seed", range(20))
def test_multiply_and_divide_match_python(seed):
    rng = random.Random(seed)
    for _ in range(50):
        a = rng.randrange(0, 0x100000000) & 0xFFFFFFFF
        b = rng.randrange(1, 0x100000000) & 0xFFFFFFFF
        if s32(b) == 0:
            continue

        cpu = CPU()
        cpu.load(0x1000, encode("MR", 4, 6))
        cpu.r[5] = a          # the multiplicand is the ODD register
        cpu.r[6] = b
        cpu.psw.ia = 0x1000
        cpu.step()
        product = s32(a) * s32(b)
        assert cpu.r[4] == (product >> 32) & 0xFFFFFFFF
        assert cpu.r[5] == product & 0xFFFFFFFF

        # Division: truncating, remainder taking the dividend's sign.
        dividend = rng.randrange(-(2**40), 2**40)
        cpu = CPU()
        cpu.load(0x1000, encode("DR", 4, 6))
        cpu.r[4] = (dividend >> 32) & 0xFFFFFFFF
        cpu.r[5] = dividend & 0xFFFFFFFF
        cpu.r[6] = b
        cpu.psw.ia = 0x1000
        cpu.step()
        q = abs(dividend) // abs(s32(b))
        if (dividend < 0) != (s32(b) < 0):
            q = -q
        r = dividend - q * s32(b)
        if -2**31 <= q < 2**31:
            # Remainder in the even register, quotient in the odd one.
            assert cpu.r[4] == r & 0xFFFFFFFF
            assert cpu.r[5] == q & 0xFFFFFFFF
