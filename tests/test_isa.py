"""Encoding tests.

Every opcode here was read from IBM GA22-7000-4, Appendix C, printed pages
262-267, cross-checked against two OCR passes and the page images. If one of
these fails, either the table in isa.py or my reading of the PoP is wrong.
"""

from __future__ import annotations

import pytest

from titor370.isa import Fmt, OPCODES, decode, encode

# Mnemonic -> (opcode, format), transcribed from the PoP listing.
POP_TABLE = {
    # RR
    "BALR": (0x05, Fmt.RR), "BCTR": (0x06, Fmt.RR), "BCR": (0x07, Fmt.RR),
    "SVC": (0x0A, Fmt.RR), "MVCL": (0x0E, Fmt.RR), "CLCL": (0x0F, Fmt.RR),
    "LPR": (0x10, Fmt.RR), "LNR": (0x11, Fmt.RR), "LTR": (0x12, Fmt.RR),
    "LCR": (0x13, Fmt.RR), "NR": (0x14, Fmt.RR), "CLR": (0x15, Fmt.RR),
    "OR": (0x16, Fmt.RR), "XR": (0x17, Fmt.RR), "LR": (0x18, Fmt.RR),
    "CR": (0x19, Fmt.RR), "AR": (0x1A, Fmt.RR), "SR": (0x1B, Fmt.RR),
    "MR": (0x1C, Fmt.RR), "DR": (0x1D, Fmt.RR), "ALR": (0x1E, Fmt.RR),
    "SLR": (0x1F, Fmt.RR),
    # RX
    "STH": (0x40, Fmt.RX), "LA": (0x41, Fmt.RX), "STC": (0x42, Fmt.RX),
    "IC": (0x43, Fmt.RX), "BAL": (0x45, Fmt.RX), "BCT": (0x46, Fmt.RX),
    "BC": (0x47, Fmt.RX), "LH": (0x48, Fmt.RX), "CH": (0x49, Fmt.RX),
    "AH": (0x4A, Fmt.RX), "SH": (0x4B, Fmt.RX), "MH": (0x4C, Fmt.RX),
    "CVD": (0x4E, Fmt.RX), "CVB": (0x4F, Fmt.RX), "ST": (0x50, Fmt.RX),
    "N": (0x54, Fmt.RX), "CL": (0x55, Fmt.RX), "O": (0x56, Fmt.RX),
    "X": (0x57, Fmt.RX), "L": (0x58, Fmt.RX), "C": (0x59, Fmt.RX),
    "A": (0x5A, Fmt.RX), "S": (0x5B, Fmt.RX), "M": (0x5C, Fmt.RX),
    "D": (0x5D, Fmt.RX), "AL": (0x5E, Fmt.RX), "SL": (0x5F, Fmt.RX),
    # RS
    "BXH": (0x86, Fmt.RS), "BXLE": (0x87, Fmt.RS), "SRL": (0x88, Fmt.RS),
    "SLL": (0x89, Fmt.RS), "SRA": (0x8A, Fmt.RS), "SLA": (0x8B, Fmt.RS),
    "SRDL": (0x8C, Fmt.RS), "SLDL": (0x8D, Fmt.RS), "SRDA": (0x8E, Fmt.RS),
    "SLDA": (0x8F, Fmt.RS), "STM": (0x90, Fmt.RS), "LM": (0x98, Fmt.RS),
    "CS": (0xBA, Fmt.RS), "CLM": (0xBD, Fmt.RS), "STCM": (0xBE, Fmt.RS),
    "ICM": (0xBF, Fmt.RS),
    # SI
    "TM": (0x91, Fmt.SI), "MVI": (0x92, Fmt.SI), "NI": (0x94, Fmt.SI),
    "CLI": (0x95, Fmt.SI), "OI": (0x96, Fmt.SI), "XI": (0x97, Fmt.SI),
    # SS
    "MVN": (0xD1, Fmt.SS), "MVC": (0xD2, Fmt.SS), "MVZ": (0xD3, Fmt.SS),
    "NC": (0xD4, Fmt.SS), "CLC": (0xD5, Fmt.SS), "OC": (0xD6, Fmt.SS),
    "XC": (0xD7, Fmt.SS), "TR": (0xDC, Fmt.SS), "TRT": (0xDD, Fmt.SS),
    "PACK": (0xF2, Fmt.SS), "UNPK": (0xF3, Fmt.SS),
    # S
    "SSM": (0x80, Fmt.S), "LPSW": (0x82, Fmt.S), "TS": (0x93, Fmt.S),
}


@pytest.mark.parametrize("name,expected", sorted(POP_TABLE.items()))
def test_opcode_matches_principles_of_operation(name, expected):
    assert name in OPCODES, f"{name} missing from the ISA table"
    assert OPCODES[name] == expected


def test_known_machine_encodings():
    # Hand-checked against the PoP instruction descriptions.
    assert encode("AR", 1, 2) == bytes.fromhex("1A12")
    assert encode("L", 1, 0, 0, 0x020) == bytes.fromhex("58100020")
    assert encode("LA", 2, 0, 12, 0x020) == bytes.fromhex("4120C020")
    assert encode("MVC", 0x0A, 0, 0x021, 0, 0x016) == bytes.fromhex("D20A00210016")
    assert encode("BCR", 15, 14) == bytes.fromhex("07FE")
    assert encode("BALR", 12, 0) == bytes.fromhex("05C0")


@pytest.mark.parametrize("name", sorted(OPCODES))
def test_encode_decode_roundtrip(name):
    _, fmt = OPCODES[name]
    if fmt is Fmt.RR:
        data = encode(name, 3, 7)
    elif fmt is Fmt.RX:
        data = encode(name, 3, 5, 9, 0x123)
    elif fmt is Fmt.RS:
        data = encode(name, 3, 5, 9, 0x123)
    elif fmt is Fmt.SI:
        data = encode(name, 0x5A, 9, 0x123)
    elif fmt is Fmt.SS:
        data = encode(name, 0x0B, 9, 0x123, 5, 0x456)
    else:
        data = encode(name, 9, 0x123)
    store = bytearray(0x2000)
    store[0x1000 : 0x1000 + len(data)] = data
    ins = decode(store, 0x1000)
    assert ins.name == name
    assert ins.length == len(data)
    assert ins.raw == data


def test_unknown_opcode_raises():
    with pytest.raises(ValueError):
        decode(bytearray(0x10), 0)
