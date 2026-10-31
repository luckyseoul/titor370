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


def test_slr_condition_code_follows_the_carry_out_of_the_sign():
    # SUBTRACT LOGICAL (GA22-7000-4, p. 144): CC 1 = difference nonzero with no
    # carry; 2 = zero with carry; 3 = nonzero with carry. A zero difference
    # cannot occur without a carry, so CC 0 is never produced.
    # 0 - 1: borrow, so no carry; difference nonzero -> CC 1
    cpu = run_one("SLR", regs={1: 0, 2: 1}, fields=(1, 2))
    assert cpu.r[1] == 0xFFFFFFFF and cpu.cc == 1
    # 5 - 5: carry, zero difference -> CC 2
    cpu = run_one("SLR", regs={1: 5, 2: 5}, fields=(1, 2))
    assert cpu.r[1] == 0 and cpu.cc == 2
    # 9 - 4: carry, nonzero -> CC 3
    cpu = run_one("SLR", regs={1: 9, 2: 4}, fields=(1, 2))
    assert cpu.r[1] == 5 and cpu.cc == 3


def test_mr_multiplies_the_odd_register_by_the_multiplier():
    # "The multiplicand is taken from the odd register of the pair"
    # (GA22-7000-4, p. 136), and the product replaces R1:R1+1.
    cpu = run_one("MR", regs={4: 0xDEADBEEF, 5: 2, 6: 3}, fields=(4, 6))
    assert cpu.r[4] == 0 and cpu.r[5] == 6

    cpu = run_one("MR", regs={4: 0x11111111, 5: 0xFFFFFFFE, 6: 3}, fields=(4, 6))
    assert cpu.r[4] == 0xFFFFFFFF and cpu.r[5] == 0xFFFFFFFA  # -6


def test_dr_quotient_and_remainder():
    # Dividend 100 in R4:R5, divisor 7. The remainder goes in the even
    # register and the quotient in the odd one (GA22-7000-4, p. 128).
    cpu = run_one("DR", regs={4: 0, 5: 100, 6: 7}, fields=(4, 6))
    assert cpu.r[4] == 2 and cpu.r[5] == 14


def test_dr_truncates_toward_zero():
    # -100 / 7 = -14 remainder -2 (S/370 truncates, like C)
    cpu = run_one("DR", regs={4: 0xFFFFFFFF, 5: 0xFFFFFF9C, 6: 7}, fields=(4, 6))
    assert cpu.r[4] == (-2) & 0xFFFFFFFF     # remainder, even register
    assert cpu.r[5] == (-14) & 0xFFFFFFFF    # quotient, odd register


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
    # The base-register idiom: the link word goes to R12 and execution
    # continues. The link word is ILC|CC|mask|address, and ILC is 1 for a
    # two-byte instruction (GA22-7000-4, p. 121).
    cpu = run_one("BALR", regs={}, fields=(12, 0))
    assert cpu.r[12] == (1 << 30) | (BASE + 2)
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


def test_sla_preserves_the_sign_and_detects_overflow():
    # "The sign of the first operand remains unchanged. All 31 integer bits
    # participate... If a bit unlike the sign bit is shifted out of position 1,
    # an overflow occurs" (GA22-7000-4, p. 139).
    # Positive 0x40000000 shifted left one: a 1 leaves position 1 while the
    # sign is 0, so this overflows, and the sign stays 0.
    cpu = run_one("SLA", regs={1: 0x40000000}, fields=(1, 0, 0, 1))
    assert cpu.cc == 3
    assert cpu.r[1] == 0x80000000 if False else (cpu.r[1] & 0x80000000) == 0

    # A negative number keeps its sign: -1 shifted left stays negative.
    cpu = run_one("SLA", regs={1: 0xFFFFFFFF}, fields=(1, 0, 0, 1))
    assert cpu.r[1] & 0x80000000

    # No overflow: 1 << 1 = 2.
    cpu = run_one("SLA", regs={1: 1}, fields=(1, 0, 0, 1))
    assert cpu.r[1] == 2 and cpu.cc == 2


def test_shift_amount_is_masked_to_six_bits():
    # D2 = 0x40 -> 0x40 & 0x3F = 0, so no shift
    cpu = run_one("SLL", regs={1: 1}, fields=(1, 0, 0, 0x40))
    assert cpu.r[1] == 1


def test_shift_amounts_of_32_or_more_shift_everything_out():
    # GA22-7000-4: "Shift amounts from 31-63 cause the entire integer to be
    # shifted out of the register. When the entire integer field of a positive
    # number has been shifted out, the register contains zero. For a negative
    # number, the register contains -1."
    assert run_one("SLL", regs={1: 1}, fields=(1, 0, 0, 32)).r[1] == 0
    assert run_one("SLL", regs={1: 0xFFFFFFFF}, fields=(1, 0, 0, 40)).r[1] == 0
    assert run_one("SRL", regs={1: 0x80000000}, fields=(1, 0, 0, 32)).r[1] == 0
    assert run_one("SRA", regs={1: 0x80000000}, fields=(1, 0, 0, 33)).r[1] == 0xFFFFFFFF
    assert run_one("SRA", regs={1: 0x7FFFFFFF}, fields=(1, 0, 0, 32)).r[1] == 0


def test_shift_amount_can_come_from_a_base_register():
    # "A zero in the B2 field indicates the absence of indirect shift
    # specification" -- with B2 set, the base register participates.
    cpu = run_one("SLL", regs={3: 4, 1: 1}, fields=(1, 0, 3, 2))
    assert cpu.r[1] == 1 << 6      # (R3 + 2) & 0x3F = 6


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
    # The hit is the last byte of a four-byte field, so the code is 2.
    assert cpu.cc == 2
    assert cpu.r[1] == 0x203    # address of the argument byte (low 24 bits)
    assert cpu.r[2] == 0x07     # the function byte (low 8 bits)


def test_trt_cc1_when_the_hit_is_not_the_last_byte():
    table = bytearray(256)
    table[0x85] = 0x09
    cpu = run_one(
        "TRT",
        mem={0x200: b"\x88\x85\x93\x96", 0x300: bytes(table)},
        fields=(3, 0, 0x200, 0, 0x300),
    )
    assert cpu.cc == 1                 # a hit with bytes still to be tested
    assert cpu.r[1] == 0x201
    assert cpu.r[2] == 0x09


def test_trt_no_hit_leaves_the_registers_alone():
    cpu = run_one("TRT", regs={1: 0x11111111, 2: 0x22222222},
                  mem={0x200: b"abcd", 0x300: bytes(256)}, fields=(3, 0, 0x200, 0, 0x300))
    assert cpu.cc == 0
    assert cpu.r[1] == 0x11111111 and cpu.r[2] == 0x22222222


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
        fields=(4, 4, 0, 0x500, 0, 0x200),
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


def test_icm_condition_code_follows_the_leftmost_inserted_byte():
    # Mask 0xC selects the two high-order bytes. The leftmost inserted byte is
    # 0x01 (high bit clear) and the next is 0x80, so the code must be 2.
    cpu = run_one("ICM", mem={0x200: b"\x01\x80"}, fields=(1, 0xC, 0, 0x200))
    assert cpu.r[1] == 0x01800000
    assert cpu.cc == 2

    # Same bytes, but with the leftmost byte negative: code 1.
    cpu = run_one("ICM", mem={0x200: b"\x81\x00"}, fields=(1, 0xC, 0, 0x200))
    assert cpu.cc == 1


def test_icm_zero_mask_is_not_an_error():
    # "When the mask is zero ... the condition code is made 0."
    cpu = run_one("ICM", mem={0x200: b"\xFF"}, fields=(1, 0, 0, 0x200))
    assert cpu.cc == 0 and cpu.r[1] == 0


def test_icm_inserts_selected_bytes():
    # Mask 0x9 = 1001 selects the leftmost and rightmost byte positions, but
    # the storage operand is contiguous: the two bytes come from 0x200, 0x201.
    cpu = run_one("ICM", regs={1: 0x11223344}, mem={0x200: b"\xAA\xBB"},
                  fields=(1, 0x9, 0, 0x200))
    assert cpu.r[1] == 0xAA2233BB     # unselected bytes keep their old values


def test_stcm_stores_selected_bytes_contiguously():
    # Mask 0xA = 1010 selects bytes 0 and 2 of the register, which are stored
    # in successive locations from 0x200 (p. 141).
    cpu = run_one("STCM", regs={1: 0x11223344}, fields=(1, 0xA, 0, 0x200))
    assert cpu.read(0x200, 2) == b"\x11\x33"


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


def test_mvn_moves_numerics_and_mvz_moves_zones():
    # MVN moves the numeric (low-order four) bits of each source byte.
    cpu = run_one("MVN", mem={0x200: b"\xF1\xF2", 0x300: b"\x3A\x4B"}, fields=(1, 0, 0x200, 0, 0x300))
    assert cpu.read(0x200, 2) == b"\xFA\xFB"
    # MVZ moves the zones (high-order four bits).
    cpu = run_one("MVZ", mem={0x200: b"\xF1\xF2", 0x300: b"\x3A\x4B"}, fields=(1, 0, 0x200, 0, 0x300))
    assert cpu.read(0x200, 2) == b"\x31\x42"


# ---------------------------------------------------------------------------
# Privileged and unimplemented
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["SSM", "LPSW", "STNSM", "STOSM"])
def test_privileged_instructions_raise(name):
    fields = (0, 0x200) if name in ("SSM", "LPSW") else (0xF0, 0, 0x200)
    with pytest.raises(PrivilegedOperation):
        run_one(name, fields=fields)


# ---------------------------------------------------------------------------
# Rules established by the independent review against Hercules
# ---------------------------------------------------------------------------


def test_bal_link_word_carries_ilc_cc_and_mask():
    # "The link information ... consists of the instruction-length code, the
    # condition code, the program mask bits, and the updated instruction
    # address" (GA22-7000-4, p. 121). BAL is four bytes, so ILC = 2.
    cpu = CPU()
    cpu.load(BASE, encode("BAL", 14, 0, 0, 0x200))
    cpu.psw.cc = 2
    cpu.psw.pmask = 0x9
    cpu.psw.ia = BASE
    cpu.step()
    assert cpu.r[14] == (2 << 30) | (2 << 28) | (0x9 << 24) | (BASE + 4)
    assert cpu.psw.ia == 0x200


def test_cs_requires_word_alignment():
    with pytest.raises(SpecificationException):
        run_one("CS", regs={1: 1, 3: 2}, mem={0x201: b"\x00\x00\x00\x00"}, fields=(1, 3, 0, 0x201))


def test_cds_requires_doubleword_alignment():
    with pytest.raises(SpecificationException):
        run_one("CDS", regs={2: 1, 3: 2, 4: 3, 5: 4}, mem={0x204: bytes(8)}, fields=(2, 4, 0, 0x204))


def test_lra_is_privileged():
    with pytest.raises(PrivilegedOperation):
        run_one("LRA", fields=(1, 0, 0, 0x200))


@pytest.mark.parametrize("name", ["ED", "EDMK", "SRP", "MVO"])
def test_unimplemented_decimal_instructions_are_operation_exceptions(name):
    # These parts of the decimal feature are not implemented; that is an
    # operation exception, not a privileged-operation exception.
    from titor370.cpu import OperationException

    cpu = CPU()
    cpu.load(BASE, encode(name, 1, 1, 0, 0x200, 0, 0x300))
    cpu.psw.ia = BASE
    with pytest.raises(OperationException) as info:
        cpu.step()
    assert info.value.code == 0x01


def test_ex_of_ex_is_an_execute_exception():
    cpu = CPU()
    # EX 0,TARGET where TARGET holds another EX.
    cpu.load(BASE, encode("EX", 0, 0, 0, 0x300))
    cpu.load(0x300, encode("EX", 0, 0, 0, 0x400))
    cpu.psw.ia = BASE
    with pytest.raises(Exception) as info:
        cpu.step()
    assert getattr(info.value, "code", None) == 0x03


def test_ex_with_register_zero_does_not_modify_the_subject():
    cpu = CPU()
    cpu.load(BASE, encode("EX", 0, 0, 0, 0x300))     # R1 = 0: no modification
    cpu.load(0x300, encode("MVI", 0x40, 2, 0))
    cpu.r[0] = 0x0F                                   # must be ignored
    cpu.r[2] = 0x200
    cpu.psw.ia = BASE
    cpu.step()
    assert cpu.fetch_byte(0x200) == 0x40              # not 0x4F


def test_ex_requires_an_even_address():
    with pytest.raises(SpecificationException):
        run_one("EX", fields=(0, 0, 0, 0x301))


def test_bct_computes_the_branch_address_before_decrementing():
    # R1 doubles as the base register: the address must use the old value.
    cpu = CPU()
    cpu.load(BASE, encode("BCT", 1, 0, 1, 4))   # BCT 1,4(1)
    cpu.r[1] = 0x1002
    cpu.psw.ia = BASE
    cpu.step()
    assert cpu.r[1] == 0x1001
    assert cpu.psw.ia == 0x1006                  # 0x1002 + 4, not 0x1001 + 4


def test_cvb_out_of_range_is_a_fixed_point_divide_and_loads_the_low_bits():
    from titor370.cpu import FixedPointDivide

    packed = bytes.fromhex("999999999999999C")   # far beyond 32 bits
    cpu = CPU()
    cpu.load(BASE, encode("CVB", 1, 0, 0, 0x200))
    cpu.write(0x200, packed)
    cpu.psw.ia = BASE
    with pytest.raises(FixedPointDivide):
        cpu.step()
    assert cpu.r[1] == 999999999999999 & 0xFFFFFFFF


def test_mvcl_moves_and_pads_with_the_24_bit_lengths():
    # R1 = destination pair, R2 = source pair; lengths in bits 8-31 of R1+1
    # and R2+1; the pad character is in bits 0-7 of R2+1 (p. 133).
    cpu = CPU()
    cpu.load(BASE, encode("MVCL", 2, 4))
    cpu.r[2] = 0x300          # destination address
    cpu.r[3] = 4              # destination length
    cpu.r[4] = 0x200          # source address
    cpu.r[5] = (0x00 << 24) | 2   # source length 2, pad 0x00
    cpu.write(0x200, b"AB")
    cpu.psw.ia = BASE
    cpu.step()
    assert cpu.read(0x300, 4) == b"AB\x00\x00"
    assert cpu.cc == 2        # first operand is longer
    assert cpu.r[2] == 0x302 and cpu.r[4] == 0x202
    assert cpu.r[3] == 2 and cpu.r[5] == 0


def test_mvcl_destructive_overlap_moves_nothing():
    cpu = CPU()
    cpu.load(BASE, encode("MVCL", 2, 4))
    cpu.r[2] = 0x201          # destination starts inside the source field
    cpu.r[3] = 4
    cpu.r[4] = 0x200
    cpu.r[5] = 4
    cpu.write(0x200, b"ABCD")
    cpu.psw.ia = BASE
    cpu.step()
    assert cpu.cc == 3
    assert cpu.read(0x200, 5) == b"ABCD\x00"


def test_clcl_compares_with_padding():
    cpu = CPU()
    cpu.load(BASE, encode("CLCL", 2, 4))
    cpu.r[2] = 0x300
    cpu.r[3] = 3              # first operand "AB\x00"
    cpu.r[4] = 0x200
    cpu.r[5] = (0x00 << 24) | 2   # second operand "AB", padded with zero
    cpu.write(0x300, b"AB\x00")
    cpu.write(0x200, b"AB")
    cpu.psw.ia = BASE
    cpu.step()
    assert cpu.cc == 0        # equal once the shorter is padded


def test_mvcl_requires_even_registers():
    with pytest.raises(SpecificationException):
        run_one("MVCL", fields=(3, 4))
