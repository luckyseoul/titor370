"""Assembler tests: addresses, symbols, USING, constants, comments."""

from __future__ import annotations

import pytest

from titor370.asm import AsmError, assemble


def test_symbols_and_entry():
    result = assemble(
        """
        ORG   0x1000
START   BALR  12,0
        DIAG  0,0,0
        END   START
        """
    )
    assert result["symbols"]["START"] == 0x1000
    assert result["entry"] == 0x1000
    assert result["image"] == bytes.fromhex("05C083000000")


def test_instruction_lengths_drive_addresses():
    result = assemble(
        """
        ORG   0x1000
        BALR  12,0
        LR    1,2
        MVC   0(4,0),0
        DIAG  0,0,0
NEXT    DS    F
        END
        """
    )
    # 2 (BALR) + 2 (LR) + 6 (MVC) + 4 (DIAG) = 14, then NEXT DS F is aligned
    # to a fullword boundary.
    assert result["symbols"]["NEXT"] == 0x1010


def test_using_turns_labels_into_displacements():
    result = assemble(
        """
        ORG   0x2000
START   BALR  12,0
        USING *,12
        L     1,TARGET
        DIAG  0,0,0
TARGET  DC    F'7'
        END   START
        """
    )
    # R12 holds 0x2002 (after the two-byte BALR). TARGET follows BALR+L+DIAG
    # = 10 bytes and is then aligned to a fullword, so it is at 0x200C and the
    # displacement from R12 is 10.
    assert result["symbols"]["TARGET"] == 0x200C
    ins = result["image"][2:6]
    assert ins == bytes.fromhex("5810C00A")


def test_constant_types():
    result = assemble(
        """
        ORG   0x1000
        DC    F'1'
        DC    H'2'
        DC    C'AB'
        DC    X'0D0A'
        DC    B'1010'
        DC    A(0x1234)
        DC    3X'FF'
        END
        """
    )
    # Constants are aligned to their natural boundaries, so A(...) is padded
    # to the next fullword after the single-byte binary constant.
    assert result["image"] == (
        bytes.fromhex("00000001")          # F'1'   at 0x1000
        + bytes.fromhex("0002")            # H'2'   at 0x1004
        + "AB".encode("cp037")             # C'AB'  at 0x1006 (EBCDIC)
        + bytes.fromhex("0D0A")            # X'..'  at 0x1008
        + bytes.fromhex("0A")              # B'..'  at 0x100A
        + bytes.fromhex("00")              # pad to a fullword
        + bytes.fromhex("00001234")        # A(...) at 0x100C
        + b"\xff\xff\xff"                 # 3X'FF'
    )


def test_constants_are_aligned():
    result = assemble(
        """
        ORG   0x1001
        DC    X'00'
        DC    H'1'
        DC    X'00'
        DC    F'2'
        END
        """
    )
    # 0x1001 byte, then a halfword aligned to 0x1002, byte at 0x1004,
    # then a fullword aligned to 0x1008.
    assert result["image"] == bytes.fromhex("0000010000000000000002")


def test_ds_zero_aligns_without_reserving():
    result = assemble(
        """
        ORG   0x1001
        DC    X'00'
        DS    0F
HERE    DC    X'FF'
        END
        """
    )
    assert result["symbols"]["HERE"] == 0x1004
    assert result["image"] == bytes.fromhex("000000FF")


def test_ds_reserves_without_emitting():
    result = assemble(
        """
        ORG   0x1000
A       DS    F
B       DS    2H
C       DC    X'00'
        END
        """
    )
    assert result["symbols"]["B"] == 0x1004
    assert result["symbols"]["C"] == 0x1008
    assert len(result["image"]) == 9


def test_comments_and_blank_lines():
    result = assemble(
        """
*        a comment line
         ORG   0x1000
;        another
START    LR    1,2            ; trailing comment
         DIAG  0,0,0
         END   START
        """
    )
    assert result["image"] == bytes.fromhex("181283000000")


def test_shift_operand_is_a_count_not_a_register():
    # SLL R1,D2(B2): the second operand is the shift amount.
    result = assemble(
        """
        ORG   0x1000
        SLL   1,32
        SRL   2,7
        SLA   3,1
        END
        """
    )
    assert result["image"] == (
        bytes.fromhex("89100020")   # SLL 1,32
        + bytes.fromhex("88200007")  # SRL 2,7
        + bytes.fromhex("8B300001")  # SLA 3,1
    )


def test_undefined_symbol_is_an_error():
    with pytest.raises(AsmError):
        assemble(
            """
            ORG   0x1000
            L     1,NOWHERE
            END
            """
        )


def test_unknown_mnemonic_is_an_error():
    with pytest.raises(AsmError):
        assemble("         FROBNICATE 1,2\n")


def test_ss_operands_must_match_in_length():
    with pytest.raises(AsmError):
        assemble(
            """
            ORG   0x1000
            MVC   0(4,0),0(2,0)
            END
            """
        )
