"""Packed-decimal arithmetic, checked against Python's own integers.

Packed fields are read and written by these tests with their own helpers, so a
decoding mistake in the emulator cannot hide by agreeing with itself.

Rules from GA22-7000-4 chapter 7: operands are packed, results never exceed the
first-operand field (surplus high-order digits are lost, which is a decimal
overflow), and results always carry the preferred sign codes 12 (plus) and
13 (minus) -- even when the value is unchanged, as when adding zero.
"""

from __future__ import annotations

import pytest

from titor370.cpu import CPU, ProgramCheck, SpecificationException
from titor370.isa import encode

BASE = 0x1000


def pack(value: int, length: int, sign: int = 0xC) -> bytes:
    """Encode *value* as a packed field of *length* bytes, independently."""
    digits = f"{abs(value):0{2 * length - 1}d}"[-max(1, 2 * length - 1):]
    nibbles = [int(d) for d in digits]
    nibbles.append(0xD if value < 0 else sign)
    out = bytearray(length)
    for index in range(length):
        out[index] = (nibbles[2 * index] << 4) | nibbles[2 * index + 1]
    return bytes(out)


def unpack(data: bytes) -> int:
    """Decode a packed field, independently of the emulator."""
    nibbles = []
    for byte in data:
        nibbles.append(byte >> 4)
        nibbles.append(byte & 0x0F)
    sign = nibbles.pop()
    value = int("".join(str(d) for d in nibbles))
    return -value if sign in (0xB, 0xD) else value


def run(name: str, first: bytes, second: bytes, pmask: int = 0):
    """Execute one SS decimal instruction on two prepared fields."""
    cpu = CPU()
    cpu.load(BASE, encode(name, len(first) - 1, len(second) - 1, 0, 0x200, 0, 0x300))
    cpu.write(0x200, first)
    cpu.write(0x300, second)
    cpu.psw.ia = BASE
    cpu.psw.pmask = pmask
    cpu.step()
    return cpu


@pytest.mark.parametrize("left,right", [
    (0, 0), (1, 1), (1234, 5678), (-1234, 5678), (1234, -5678), (-100, 3),
    (99999, 1), (-99999, -1),
])
def test_add_decimal_matches_python(left, right):
    cpu = run("AP", pack(left, 5), pack(right, 5))
    assert unpack(cpu.read(0x200, 5)) == left + right


@pytest.mark.parametrize("left,right", [
    (0, 0), (7, 3), (3, 7), (-7, 3), (7, -3), (-7, -3), (1234, 1234),
])
def test_subtract_decimal_matches_python(left, right):
    cpu = run("SP", pack(left, 5), pack(right, 5))
    assert unpack(cpu.read(0x200, 5)) == left - right


@pytest.mark.parametrize("left,right", [
    (1234, 7), (0, 5), (-1234, 7), (1234, -7), (-1234, -7), (99, 99),
])
def test_multiply_decimal_matches_python(left, right):
    # MP: product replaces the multiplicand, which must be the longer field.
    cpu = run("MP", pack(left, 6), pack(right, 2))
    assert unpack(cpu.read(0x200, 6)) == left * right


@pytest.mark.parametrize("dividend,divisor", [
    (1234, 7), (-1234, 7), (1234, -7), (-1234, -7), (100, 10), (7, 7),
])
def test_divide_decimal_matches_python(dividend, divisor):
    # Quotient in the leftmost L1-L2 bytes, remainder in the rightmost L2.
    first, second = pack(dividend, 6), pack(divisor, 2)
    cpu = run("DP", first, second)
    quotient_bytes = len(first) - len(second)
    quotient = unpack(cpu.read(0x200, quotient_bytes))
    remainder = unpack(cpu.read(0x200 + quotient_bytes, len(second)))
    q = abs(dividend) // abs(divisor)
    if (dividend < 0) != (divisor < 0):
        q = -q
    assert quotient == q
    assert remainder == dividend - q * divisor


@pytest.mark.parametrize("left,right,expected", [
    (5, 5, 0), (4, 5, 1), (6, 5, 2), (-1, 1, 1), (1, -1, 2), (0, 0, 0),
])
def test_compare_decimal(left, right, expected):
    cpu = run("CP", pack(left, 4), pack(right, 4))
    assert cpu.cc == expected
    # CP must not alter either operand.
    assert unpack(cpu.read(0x200, 4)) == left
    assert unpack(cpu.read(0x300, 4)) == right


def test_zero_and_add_copies_and_zero_extends():
    cpu = run("ZAP", bytes(4), pack(-42, 2))
    assert unpack(cpu.read(0x200, 4)) == -42


def test_results_carry_the_preferred_sign_codes():
    # "These codes are provided even when the operand value is otherwise
    # unchanged, such as when adding zero to a number" (p. 148).
    # 0xF is a valid plus sign on input but 0xC must come out.
    cpu = run("AP", pack(7, 3, sign=0xF), pack(0, 3))
    assert cpu.read(0x200, 3)[-1] & 0x0F == 0xC
    cpu = run("SP", pack(7, 3, sign=0xF), pack(0, 3))
    assert cpu.read(0x200, 3)[-1] & 0x0F == 0xC


def test_alternate_sign_nibbles_are_accepted():
    for sign in (0xA, 0xC, 0xE, 0xF):
        assert unpack(run("AP", pack(1, 3, sign), pack(1, 3)).read(0x200, 3)) == 2
    for sign in (0xB, 0xD):
        cpu = run("AP", pack(1, 3, sign), pack(1, 3))
        assert unpack(cpu.read(0x200, 3)) == 0


def test_invalid_sign_is_a_data_exception():
    bad = bytearray(pack(1, 3))
    bad[-1] = (bad[-1] & 0xF0) | 0x5      # 0x5 is not a sign code
    with pytest.raises(ProgramCheck) as info:
        run("AP", bad, pack(1, 3))
    assert info.value.code == 0x07


def test_invalid_digit_is_a_data_exception():
    bad = bytearray(pack(1, 3))
    bad[0] = 0xAB                          # A is not a decimal digit
    with pytest.raises(ProgramCheck) as info:
        run("AP", bad, pack(1, 3))
    assert info.value.code == 0x07


def test_overflow_sets_cc3_and_truncates():
    # 9999 + 1 into a three-digit field: the carry is lost.
    cpu = run("AP", pack(999, 2), pack(1, 2))
    assert cpu.cc == 3
    assert unpack(cpu.read(0x200, 2)) == 0


def test_overflow_interrupts_only_when_the_mask_bit_is_set():
    # Program mask bit 0 is decimal overflow.
    with pytest.raises(ProgramCheck) as info:
        run("AP", pack(999, 2), pack(1, 2), pmask=0x8)
    assert info.value.code == 0x0A


def test_decimal_divide_by_zero():
    with pytest.raises(ProgramCheck) as info:
        run("DP", pack(100, 4), pack(0, 2))
    assert info.value.code == 0x0B


def test_multiplier_must_be_shorter_than_the_multiplicand():
    with pytest.raises(SpecificationException):
        run("MP", pack(2, 2), pack(3, 2))


def test_divisor_must_be_shorter_than_the_dividend():
    with pytest.raises(SpecificationException):
        run("DP", pack(2, 2), pack(3, 2))


# ---------------------------------------------------------------------------
# MOVE WITH OFFSET, SHIFT AND ROUND, EDIT, EDIT AND MARK
# ---------------------------------------------------------------------------


def test_move_with_offset_shifts_by_four_bits():
    # "The low-order four bits of the first operand are attached as low-order
    # bits to the second operand" (p. 135).
    cpu = CPU()
    cpu.write(0x200, bytes.fromhex("00000C"))
    cpu.write(0x300, bytes.fromhex("345C"))
    cpu.load(BASE, encode("MVO", 2, 1, 0, 0x200, 0, 0x300))
    cpu.psw.ia = BASE
    cpu.step()
    assert cpu.read(0x200, 3) == bytes.fromhex("0345CC")


def _srp(field: bytes, shift: int, rounding: int):
    cpu = CPU()
    cpu.write(0x200, field)
    cpu.load(BASE, encode("SRP", len(field) - 1, rounding, 0, 0x200, 0, shift & 0x3F))
    cpu.psw.ia = BASE
    cpu.step()
    return cpu


@pytest.mark.parametrize("field,shift,rounding,expected", [
    (bytes.fromhex("01234C"), -2, 0, 12),     # right shift, no rounding
    (bytes.fromhex("01234C"), -2, 5, 12),     # 34 + 50 = 84: no carry
    (bytes.fromhex("01299C"), -2, 5, 13),     # 99 + 50 = 149: carry
    (bytes.fromhex("0001234C"), 1, 0, 12340),  # left shift
    (bytes.fromhex("01234C"), 0, 0, 1234),     # no shift
])
def test_shift_and_round_decimal(field, shift, rounding, expected):
    cpu = _srp(field, shift, rounding)
    assert unpack(cpu.read(0x200, len(field))) == expected


def test_shift_left_overflow_sets_cc3_and_truncates():
    cpu = _srp(bytes.fromhex("01234C"), 2, 0)   # 123400 in a five-digit field
    assert cpu.cc == 3
    assert unpack(cpu.read(0x200, 3)) == 23400


def test_edit_expands_digits_and_suppresses_leading_zeros():
    # Fill 0x40, a significance starter then four digit selectors, source 1234.
    pattern = bytes([0x40, 0x21, 0x20, 0x20, 0x20, 0x20])
    cpu = CPU()
    cpu.write(0x200, pattern)
    cpu.write(0x300, bytes.fromhex("01234C"))
    cpu.load(BASE, encode("ED", 5, 2, 0, 0x200, 0, 0x300))
    cpu.psw.ia = BASE
    cpu.step()
    # Hand-traced: the message character at 0 takes the fill; the starter sees
    # the leading 0 and also takes the fill; the four selectors then place
    # 1, 2, 3 and 4. The final digit carries the plus sign, which turns the
    # significance indicator off again.
    assert cpu.read(0x200, 6) == bytes([0x40, 0x40, 0xF1, 0xF2, 0xF3, 0xF4])
    assert cpu.cc == 2        # nonzero field, indicator off at the end


def test_edit_suppresses_leading_zeros():
    # Source 007 with selectors: the leading zeros become the fill character.
    pattern = bytes([0x40, 0x20, 0x20, 0x20])
    cpu = CPU()
    cpu.write(0x200, pattern)
    cpu.write(0x300, bytes.fromhex("007C"))
    cpu.load(BASE, encode("ED", 3, 1, 0, 0x200, 0, 0x300))
    cpu.psw.ia = BASE
    cpu.step()
    # Pattern is fill + three selectors; the first two see zeros with the
    # indicator off, so they emit the fill and only the 7 is placed.
    assert cpu.read(0x200, 4) == bytes([0x40, 0x40, 0x40, 0xF7])
    assert cpu.cc == 2


def test_edit_and_mark_records_the_first_significant_position():
    pattern = bytes([0x40, 0x20, 0x20, 0x20])
    cpu = CPU()
    cpu.write(0x200, pattern)
    cpu.write(0x300, bytes.fromhex("007C"))
    cpu.load(BASE, encode("EDMK", 3, 1, 0, 0x200, 0, 0x300))
    cpu.psw.ia = BASE
    cpu.r[1] = 0
    cpu.step()
    # The 7 is the first digit actually placed, at pattern offset 3.
    assert cpu.r[1] & 0xFFFFFF == 0x203


def test_edit_rejects_an_invalid_source_digit():
    pattern = bytes([0x40, 0x20, 0x20])
    cpu = CPU()
    cpu.write(0x200, pattern)
    cpu.write(0x300, bytes.fromhex("AB1C"))
    cpu.load(BASE, encode("ED", 2, 1, 0, 0x200, 0, 0x300))
    cpu.psw.ia = BASE
    with pytest.raises(ProgramCheck) as info:
        cpu.step()
    assert info.value.code == 0x07
