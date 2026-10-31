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
    # 2 (BALR) + 2 (LR) + 6 (MVC) + 4 (DIAG)
    assert result["symbols"]["NEXT"] == 0x1000 + 14


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
    # = 2+4+4 bytes, so it is at 0x200A and the displacement is 8.
    assert result["symbols"]["TARGET"] == 0x200A
    ins = result["image"][2:6]
    assert ins == bytes.fromhex("5810C008")


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
    assert result["image"] == (
        bytes.fromhex("00000001")
        + bytes.fromhex("0002")
        + "AB".encode("cp037")  # S/370 stores characters as EBCDIC
        + bytes.fromhex("0D0A")
        + bytes.fromhex("0A")
        + bytes.fromhex("00001234")
        + b"\xff\xff\xff"
    )


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
