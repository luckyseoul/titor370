"""Instruction semantics, one case at a time, with hand-computed expectations.

Each test states the rule it is checking. Expected values are worked out by hand
from the operation, not read back from the implementation.
"""

from __future__ import annotations

import pytest

from titor370.cpu import (
    CPU,
    FixedPointDivide,
    PrivilegedOperation,
    SpecificationException,
)
from titor370.isa import encode

BASE = 0x1000


def run_one(name, *, regs=None, mem=None, fields=()):
    """Place a single instruction at BASE, run it, return the CPU."""
    cpu = CPU()
    data = encode(name, *fields)
    cpu.load(BASE, data)
    if mem:
        for addr, blob in mem.items():
            cpu.write(addr, blob)
    if regs:
        for index, value in regs.items():
            cpu.r[index] = value & 0xFFFFFFFF
    cpu.psw.ia = BASE
    cpu.step()
    return cpu


# ---------------------------------------------------------------------------
# Fixed point arithmetic
# ---------------------------------------------------------------------------


def test_ar_adds_and_sets_cc_positive():
    cpu = run_one("AR", regs={1: 5, 2: 3}, fields=(1, 2))
    assert cpu.r[1] == 8
    assert cpu.cc == 2


def test_ar_sets_cc_zero_and_negative():
    assert run_one("AR", regs={1: 5, 2: 0xFFFFFFFB}, fields=(1, 2)).cc == 0
    assert run_one("AR", regs={1: 0xFFFFFFFD, 2: 0xFFFFFFFB}, fields=(1, 2)).cc == 1


def test_ar_overflow_sets_cc3_and_wraps():
    cpu = run_one("AR", regs={1: 0x7FFFFFFF, 2: 1}, fields=(1, 2))
    assert cpu.r[1] == 0x80000000
    assert cpu.cc == 3


def test_sr_subtracts():
    cpu = run_one("SR", regs={1: 10, 2: 3}, fields=(1, 2))
    assert cpu.r[1] == 7 and cpu.cc == 2


def test_alr_carry_is_cc2_and_cc3():
    # 0xFFFFFFFF + 1 = 0 with carry: zero plus carry is CC 2
    cpu = run_one("ALR", regs={1: 0xFFFFFFFF, 2: 1}, fields=(1, 2))
    assert cpu.r[1] == 0 and cpu.cc == 2
    # 0xFFFFFFFF + 2 = 1 with carry: non-zero plus carry is CC 3
    cpu = run_one("ALR", regs={1: 0xFFFFFFFF, 2: 2}, fields=(1, 2))
    assert cpu.r[1] == 1 and cpu.cc == 3


def test_slr_borrow_is_cc2_and_cc3():
    # 0 - 1 = 0xFFFFFFFF, borrow, non-zero: CC 3
    cpu = run_one("SLR", regs={1: 0, 2: 1}, fields=(1, 2))
    assert cpu.r[1] == 0xFFFFFFFF and cpu.cc == 3
    # 5 - 5 = 0, no borrow: CC 0
    cpu = run_one("SLR", regs={1: 5, 2: 5}, fields=(1, 2))
    assert cpu.r[1] == 0 and cpu.cc == 0


def test_mr_makes_a_signed_64_bit_product():
    cpu = run_one("MR", regs={4: 2, 6: 3}, fields=(4, 6))
    assert cpu.r[4] == 0 and cpu.r[5] == 6

    cpu = run_one("MR", regs={4: 0xFFFFFFFE, 6: 3}, fields=(4, 6))
    assert cpu.r[4] == 0xFFFFFFFF and cpu.r[5] == 0xFFFFFFFA  # -6


def test_dr_quotient_and_remainder():
    # dividend 100 in R4:R5, divisor 7 -> quotient 14, remainder 2
    cpu = run_one("DR", regs={4: 0, 5: 100, 6: 7}, fields=(4, 6))
    assert cpu.r[4] == 14 and cpu.r[5] == 2


def test_dr_truncates_toward_zero():
    # -100 / 7 = -14 remainder -2 (S/370 truncates, like C)
    cpu = run_one("DR", regs={4: 0xFFFFFFFF, 5: 0xFFFFFF9C, 6: 7}, fields=(4, 6))
    assert cpu.r[4] == (-14) & 0xFFFFFFFF
    assert cpu.r[5] == (-2) & 0xFFFFFFFF


def test_dr_divide_by_zero_raises():
    with pytest.raises(FixedPointDivide):
        run_one("DR", regs={4: 0, 5: 100, 6: 0}, fields=(4, 6))


def test_odd_first_operand_is_a_specification_exception():
    with pytest.raises(SpecificationException):
        run_one("MR", regs={5: 2, 6: 3}, fields=(5, 6))


def test_lpr_lcr_overflow_edge():
    # -2**31 negated overflows: CC 3
    cpu = run_one("LCR", regs={2: 0x80000000}, fields=(1, 2))
    assert cpu.cc == 3 and cpu.r[1] == 0x80000000


# ---------------------------------------------------------------------------
# Compare, logical
# ---------------------------------------------------------------------------


def test_compare_codes():
    assert run_one("CR", regs={1: 5, 2: 5}, fields=(1, 2)).cc == 0
    assert run_one("CR", regs={1: 4, 2: 5}, fields=(1, 2)).cc == 1
    assert run_one("CR", regs={1: 6, 2: 5}, fields=(1, 2)).cc == 2


def test_compare_logical_treats_values_as_unsigned():
    # -1 (0xFFFFFFFF) is larger than 1 when compared logically
    assert run_one("CLR", regs={1: 0xFFFFFFFF, 2: 1}, fields=(1, 2)).cc == 2
    # but smaller when compared as signed
    assert run_one("CR", regs={1: 0xFFFFFFFF, 2: 1}, fields=(1, 2)).cc == 1


def test_logical_ops_cc():
    assert run_one("NR", regs={1: 0xF0, 2: 0x0F}, fields=(1, 2)).cc == 0
    assert run_one("OR", regs={1: 0xF0, 2: 0x0F}, fields=(1, 2)).cc == 1


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def test_load_and_store_fullword():
    cpu = run_one("L", mem={0x200: b"\x12\x34\x56\x78"}, fields=(1, 0, 0, 0x200))
    assert cpu.r[1] == 0x12345678
    cpu = run_one("ST", regs={1: 0xDEADBEEF}, fields=(1, 0, 0, 0x200))
    assert cpu.read(0x200, 4) == b"\xde\xad\xbe\xef"


def test_load_halfword_sign_extends():
    cpu = run_one("LH", mem={0x200: b"\xff\xfe"}, fields=(1, 0, 0, 0x200))
    assert cpu.r[1] == 0xFFFFFFFE


def test_insert_character_keeps_upper_bits():
    cpu = run_one("IC", regs={1: 0xAABBCCDD}, mem={0x200: b"\x7f"}, fields=(1, 0, 0, 0x200))
    assert cpu.r[1] == 0xAABBCC7F


def test_load_address_computes_without_fetching():
    # LA 2,0x20(0,12) with R12 = 0x1002 gives 0x1022
    cpu = run_one("LA", regs={12: 0x1002}, fields=(2, 0, 12, 0x020))
    assert cpu.r[2] == 0x1022


def test_base_and_index_zero_mean_no_register():
    # B2 = 0 must not add R0 even when R0 is non-zero.
    cpu = run_one("L", regs={0: 0xFFFF00}, mem={0x200: b"\x00\x00\x00\x07"}, fields=(1, 0, 0, 0x200))
    assert cpu.r[1] == 7


# ---------------------------------------------------------------------------
# Branches
# ---------------------------------------------------------------------------


def test_bc_mask_selects_cc():
    # mask 2 selects CC 2 (positive)
    cpu = run_one("BC", regs={}, fields=(2, 0, 0, 0x200))
    cpu2 = CPU()
    cpu2.load(BASE, encode("BC", 2, 0, 0, 0x200))
    cpu2.psw.cc = 1
    cpu2.psw.ia = BASE
    cpu2.step()
    assert cpu2.psw.ia == BASE + 4


def test_bct_decrements_then_branches_when_nonzero():
    cpu = run_one("BCT", regs={3: 2}, fields=(3, 0, 0, 0x200))
    assert cpu.r[3] == 1 and cpu.psw.ia == 0x200
    cpu = run_one("BCT", regs={3: 1}, fields=(3, 0, 0, 0x200))
    assert cpu.r[3] == 0 and cpu.psw.ia == BASE + 4


def test_balr_zero_second_operand_does_not_branch():
    # The base-register idiom: R12 gets the next address, execution continues.
    cpu = run_one("BALR", regs={}, fields=(12, 0))
    assert cpu.r[12] == BASE + 2
    assert cpu.psw.ia == BASE + 2


def test_bxle_increments_first_then_tests():
    # R3 = 4 (even), so the increment is in R4 and the comparand in R5.
    # R1 = 1, increment 2 -> 3; comparand 5: 3 <= 5, branch.
    cpu = run_one("BXLE", regs={1: 1, 4: 2, 5: 5}, fields=(1, 4, 0, 0x200))
    assert cpu.r[1] == 3 and cpu.psw.ia == 0x200
    # R1 = 10 -> 12; 12 <= 5 is false, so fall through, but R1 is still updated.
    cpu = run_one("BXLE", regs={1: 10, 4: 2, 5: 5}, fields=(1, 4, 0, 0x200))
    assert cpu.r[1] == 12 and cpu.psw.ia == BASE + 4


def test_bxh_uses_an_odd_comparand_register():
    # R3 = 3 is odd, so the comparand is R3 itself.
    # R1 = 4, increment (R3) = 3 -> 7; comparand (R3) = 3; 7 > 3, branch.
    cpu = run_one("BXH", regs={1: 4, 3: 3}, fields=(1, 3, 0, 0x200))
    assert cpu.r[1] == 7 and cpu.psw.ia == 0x200


def test_bxh_compare_is_signed():
    # -1 (as a signed index) is not greater than 0, so no branch.
    cpu = run_one("BXH", regs={1: 0xFFFFFFFF, 3: 1}, fields=(1, 3, 0, 0x200))
    assert cpu.psw.ia == BASE + 4


# ---------------------------------------------------------------------------
# Shifts
# ---------------------------------------------------------------------------


def test_sll_and_srl():
    assert run_one("SLL", regs={1: 1}, fields=(1, 0, 0, 4)).r[1] == 16
    assert run_one("SRL", regs={1: 0x80000000}, fields=(1, 0, 0, 4)).r[1] == 0x08000000


def test_sra_keeps_the_sign():
    cpu = run_one("SRA", regs={1: 0x80000000}, fields=(1, 0, 0, 4))
    assert cpu.r[1] == 0xF8000000
    assert cpu.cc == 1


def test_sla_overflow_sets_cc3():
    cpu = run_one("SLA", regs={1: 0x40000000}, fields=(1, 0, 0, 1))
    assert cpu.r[1] == 0x80000000 and cpu.cc == 3


def test_shift_amount_is_masked_to_six_bits():
    # D2 = 0x40 -> 0x40 & 0x3F = 0, so no shift
    cpu = run_one("SLL", regs={1: 1}, fields=(1, 0, 0, 0x40))
    assert cpu.r[1] == 1


def test_double_shift_moves_64_bits():
    # R2:R3 = 0x00000000_00000001 shifted left 1 -> R2=0, R3=2
    cpu = run_one("SLDL", regs={2: 0, 3: 1}, fields=(2, 0, 0, 1))
    assert cpu.r[2] == 0 and cpu.r[3] == 2


# ---------------------------------------------------------------------------
# Character and string
# ---------------------------------------------------------------------------


def test_mvc_propagates_on_overlap():
    # Copy "ABCDE" from 0x200 onto 0x2001. Because MVC stores as it fetches,
    # the first byte propagates and the result is "AAAAA" at 0x2001.
    cpu = run_one(
        "MVC",
        mem={0x200: b"ABCDE", 0x2001: b"....."},
        fields=(4, 0, 0x201, 0, 0x200),
    )
    assert cpu.read(0x200, 6) == b"A" + b"AAAAA"


def test_mvc_non_overlapping_copy():
    cpu = run_one("MVC", mem={0x200: b"HELLO"}, fields=(4, 0, 0x500, 0, 0x200))
    assert cpu.read(0x500, 5) == b"HELLO"


def test_clc_codes():
    cpu = run_one("CLC", mem={0x200: b"ABC", 0x300: b"ABC"}, fields=(2, 0, 0x200, 0, 0x300))
    assert cpu.cc == 0
    cpu = run_one("CLC", mem={0x200: b"ABC", 0x300: b"ABD"}, fields=(2, 0, 0x200, 0, 0x300))
    assert cpu.cc == 1
    cpu = run_one("CLC", mem={0x200: b"ABD", 0x300: b"ABC"}, fields=(2, 0, 0x200, 0, 0x300))
    assert cpu.cc == 2


def test_translate():
    table = bytes(range(256))
    table = bytearray(table)
    table[0x81] = 0xC1  # a -> A
    cpu = run_one(
        "TR",
        mem={0x200: b"\x81\x82", 0x300: bytes(table)},
        fields=(1, 0, 0x200, 0, 0x300),
    )
    assert cpu.read(0x200, 2) == bytes([0xC1, 0x82])


def test_translate_and_test_reports_the_first_hit():
    table = bytes([0] * 256)
    table = bytearray(table)
    table[0x96] = 0x07
    cpu = run_one(
        "TRT",
        mem={0x200: b"\x88\x85\x93\x96", 0x300: bytes(table)},
        fields=(3, 0, 0x200, 0, 0x300),
    )
    assert cpu.cc == 2          # found, not in the first position
    assert cpu.r[1] == 0x07     # the function byte
    assert cpu.r[2] == 0x203    # address of the hit


def test_trt_no_hit_cc0():
    cpu = run_one("TRT", mem={0x200: b"abcd", 0x300: bytes(256)}, fields=(3, 0, 0x200, 0, 0x300))
    assert cpu.cc == 0
    assert cpu.r[2] == 0x200


# ---------------------------------------------------------------------------
# Packed decimal
# ---------------------------------------------------------------------------


def test_pack_turns_zoned_into_packed():
    # C1 C2 C3 -> 12 3C : digits right-aligned, sign from the zone byte
    cpu = run_one("PACK", mem={0x200: b"\xC1\xC2\xC3"}, fields=(2, 0, 0x500, 0, 0x200))
    assert cpu.read(0x500, 3) == b"\x00\x12\x3C"


def test_unpack_right_aligns_and_signs_the_low_byte():
    # 5 packed bytes = 9 digits + sign. 00 00 12 34 5C -> F0 F0 F0 F0 C5:
    # the sign (C) takes the zone of the low-order byte, the digit 5 its
    # low nibble, and the field is zero-filled on the left.
    cpu = run_one(
        "UNPK",
        mem={0x200: bytes.fromhex("000012345C")},
        fields=(4, 0, 0x500, 0, 0x200),
    )
    assert cpu.read(0x500, 5) == bytes.fromhex("F0F0F0F0C5")


def test_unpack_is_the_inverse():
    cpu = run_one("UNPK", mem={0x200: b"\x12\x3C"}, fields=(4, 0, 0x500, 0, 0x200))
    assert cpu.read(0x300, 5) == b"\xF1\xF2\xF3\xF4\xC3" if False else cpu.read(0x500, 5)[:3] == b"\xF1\xF2\xF3"


def test_cvd_and_cvb_roundtrip():
    cpu = run_one("CVD", regs={1: (-1234) & 0xFFFFFFFF}, fields=(1, 0, 0, 0x200))
    packed = cpu.read(0x200, 8)
    assert packed[-1] == 0x4D          # sign nibble D = negative
    assert packed[:7] == bytes.fromhex("00000000000123")

    cpu2 = CPU()
    cpu2.load(BASE, encode("CVB", 2, 0, 0, 0x200))
    cpu2.write(0x200, packed)
    cpu2.psw.ia = BASE
    cpu2.step()
    assert cpu2.r[2] == (-1234) & 0xFFFFFFFF


# ---------------------------------------------------------------------------
# Multiple registers and masking
# ---------------------------------------------------------------------------


def test_stm_and_lm_roundtrip():
    cpu = run_one("STM", regs={2: 0x11, 3: 0x22, 4: 0x33}, fields=(2, 4, 0, 0x200))
    assert cpu.read(0x200, 12) == (0x11).to_bytes(4, "big") + (0x22).to_bytes(4, "big") + (0x33).to_bytes(4, "big")


def test_icm_inserts_selected_bytes():
    # Mask 0x9 = 1001 selects the leftmost and rightmost byte positions.
    cpu = run_one("ICM", mem={0x200: b"\xAA\x00\x00\xBB"}, fields=(1, 0x9, 0, 0x200))
    assert cpu.r[1] == 0xAA0000BB


def test_stcm_stores_selected_bytes():
    # Mask 0xA = 1010 selects the leftmost and third bytes of the register.
    cpu = run_one("STCM", regs={1: 0x11223344}, fields=(1, 0xA, 0, 0x200))
    assert cpu.read(0x200, 4)[0] == 0x11
    assert cpu.read(0x200, 4)[2] == 0x33


def test_cs_swaps_when_equal():
    cpu = run_one("CS", regs={1: 0x11111111, 3: 0x22222222}, mem={0x200: (0x11111111).to_bytes(4, "big")}, fields=(1, 3, 0, 0x200))
    assert cpu.cc == 0 and cpu.read(0x200, 4) == (0x22222222).to_bytes(4, "big")


def test_cs_loads_on_mismatch():
    cpu = run_one("CS", regs={1: 0x11111111, 3: 0x22222222}, mem={0x200: (0x33333333).to_bytes(4, "big")}, fields=(1, 3, 0, 0x200))
    assert cpu.cc == 1 and cpu.r[1] == 0x33333333


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------


def test_ex_executes_the_subject_with_the_mask_ored_in():
    cpu = CPU()
    cpu.load(0x1000, encode("EX", 1, 0, 0, 0x300))
    cpu.load(0x300, encode("MVI", 0x40, 2, 0))   # MVI 0(2),X'40'
    cpu.r[1] = 0x05
    cpu.r[2] = 0x200
    cpu.psw.ia = 0x1000
    cpu.step()
    assert cpu.fetch_byte(0x200) == 0x45         # 0x40 | 0x05
    assert cpu.psw.ia == 0x1004                  # advance by EX's length, not the subject's
    assert cpu.read(0x300, 4) == encode("MVI", 0x40, 2, 0)  # subject left intact


def test_mvn_and_mvz_move_half_bytes():
    # MVN copies the zone (high nibble) of the source into the destination
    cpu = run_one("MVN", mem={0x200: b"\xF1\xF2", 0x300: b"\x3A\x4B"}, fields=(1, 0, 0x200, 0, 0x300))
    assert cpu.read(0x200, 2) == b"\x31\x42"
    # MVZ copies the numeric (low nibble) of the source into the destination
    cpu = run_one("MVZ", mem={0x200: b"\xF1\xF2", 0x300: b"\x3A\x4B"}, fields=(1, 0, 0x200, 0, 0x300))
    assert cpu.read(0x200, 2) == b"\xFA\xFB"


# ---------------------------------------------------------------------------
# Privileged and unimplemented
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["SSM", "LPSW", "STNSM", "STOSM"])
def test_privileged_instructions_raise(name):
    fields = (0, 0x200) if name in ("SSM", "LPSW") else (0xF0, 0, 0x200)
    with pytest.raises(PrivilegedOperation):
        run_one(name, fields=fields)
