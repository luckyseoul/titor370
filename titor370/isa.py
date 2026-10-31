"""S/370 instruction set: formats, opcodes, and operand decoding.

Encodings in this table were verified against IBM GA22-7000-4, *IBM System/370
Principles of Operation*, September 1975 -- Appendix C, "Instructions Arranged by
Operation Code" (printed pages 262-267). They were not written from memory: two
independent OCR passes of that appendix were cross-checked, and the resulting
table was confirmed against rendered page images.

Tools/pop_opcodes_ocr.json holds the raw OCR rows for audit.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Fmt(str, Enum):
    RR = "RR"   # op | R1 R2 | --
    RX = "RX"   # op | R1 X2 | B2 D2
    RS = "RS"   # op | R1 R3 | B2 D2
    SI = "SI"   # op | I2    | B2 D2
    SS = "SS"   # op | L     | B1 D1 | B2 D2
    S = "S"     # op | --    | B2 D2


# name -> (opcode, format).  Opcodes are the first byte, except for the few
# two-byte operations declared in TWO_BYTE_OPS below.
OPCODES: dict[str, tuple[int, Fmt]] = {
    # ---- RR ----
    "SPM": (0x04, Fmt.RR),
    "BALR": (0x05, Fmt.RR),
    "BCTR": (0x06, Fmt.RR),
    "BCR": (0x07, Fmt.RR),
    "SSK": (0x08, Fmt.RR),
    "ISK": (0x09, Fmt.RR),
    "SVC": (0x0A, Fmt.RR),
    "MVCL": (0x0E, Fmt.RR),
    "CLCL": (0x0F, Fmt.RR),
    "LPR": (0x10, Fmt.RR),
    "LNR": (0x11, Fmt.RR),
    "LTR": (0x12, Fmt.RR),
    "LCR": (0x13, Fmt.RR),
    "NR": (0x14, Fmt.RR),
    "CLR": (0x15, Fmt.RR),
    "OR": (0x16, Fmt.RR),
    "XR": (0x17, Fmt.RR),
    "LR": (0x18, Fmt.RR),
    "CR": (0x19, Fmt.RR),
    "AR": (0x1A, Fmt.RR),
    "SR": (0x1B, Fmt.RR),
    "MR": (0x1C, Fmt.RR),
    "DR": (0x1D, Fmt.RR),
    "ALR": (0x1E, Fmt.RR),
    "SLR": (0x1F, Fmt.RR),
    # ---- RX ----
    "STH": (0x40, Fmt.RX),
    "LA": (0x41, Fmt.RX),
    "STC": (0x42, Fmt.RX),
    "IC": (0x43, Fmt.RX),
    "EX": (0x44, Fmt.RX),
    "BAL": (0x45, Fmt.RX),
    "BCT": (0x46, Fmt.RX),
    "BC": (0x47, Fmt.RX),
    "LH": (0x48, Fmt.RX),
    "CH": (0x49, Fmt.RX),
    "AH": (0x4A, Fmt.RX),
    "SH": (0x4B, Fmt.RX),
    "MH": (0x4C, Fmt.RX),
    "CVD": (0x4E, Fmt.RX),
    "CVB": (0x4F, Fmt.RX),
    "ST": (0x50, Fmt.RX),
    "N": (0x54, Fmt.RX),
    "CL": (0x55, Fmt.RX),
    "O": (0x56, Fmt.RX),
    "X": (0x57, Fmt.RX),
    "L": (0x58, Fmt.RX),
    "C": (0x59, Fmt.RX),
    "A": (0x5A, Fmt.RX),
    "S": (0x5B, Fmt.RX),
    "M": (0x5C, Fmt.RX),
    "D": (0x5D, Fmt.RX),
    "AL": (0x5E, Fmt.RX),
    "SL": (0x5F, Fmt.RX),
    # ---- RS ----
    "LRA": (0xB1, Fmt.RX),
    "BXH": (0x86, Fmt.RS),
    "BXLE": (0x87, Fmt.RS),
    "SRL": (0x88, Fmt.RS),
    "SLL": (0x89, Fmt.RS),
    "SRA": (0x8A, Fmt.RS),
    "SLA": (0x8B, Fmt.RS),
    "SRDL": (0x8C, Fmt.RS),
    "SLDL": (0x8D, Fmt.RS),
    "SRDA": (0x8E, Fmt.RS),
    "SLDA": (0x8F, Fmt.RS),
    "STM": (0x90, Fmt.RS),
    "LM": (0x98, Fmt.RS),
    "CS": (0xBA, Fmt.RS),
    "CDS": (0xBB, Fmt.RS),
    "CLM": (0xBD, Fmt.RS),
    "STCM": (0xBE, Fmt.RS),
    "ICM": (0xBF, Fmt.RS),
    # ---- SS ----  (verified against PoP Appendix C, printed p. 265)
    "MVN": (0xD1, Fmt.SS),
    "MVC": (0xD2, Fmt.SS),
    "MVZ": (0xD3, Fmt.SS),
    "NC": (0xD4, Fmt.SS),
    "CLC": (0xD5, Fmt.SS),
    "OC": (0xD6, Fmt.SS),
    "XC": (0xD7, Fmt.SS),
    "TR": (0xDC, Fmt.SS),
    "TRT": (0xDD, Fmt.SS),
    "ED": (0xDE, Fmt.SS),
    "EDMK": (0xDF, Fmt.SS),
    "SRP": (0xF0, Fmt.SS),
    "MVO": (0xF1, Fmt.SS),
    "PACK": (0xF2, Fmt.SS),
    "UNPK": (0xF3, Fmt.SS),
    "ZAP": (0xF8, Fmt.SS),
    "CP": (0xF9, Fmt.SS),
    "AP": (0xFA, Fmt.SS),
    "SP": (0xFB, Fmt.SS),
    "MP": (0xFC, Fmt.SS),
    "DP": (0xFD, Fmt.SS),
    # ---- SI ----
    "TM": (0x91, Fmt.SI),
    "MVI": (0x92, Fmt.SI),
    "NI": (0x94, Fmt.SI),
    "CLI": (0x95, Fmt.SI),
    "OI": (0x96, Fmt.SI),
    "XI": (0x97, Fmt.SI),
    "STNSM": (0xAC, Fmt.SI),
    "STOSM": (0xAD, Fmt.SI),
    # ---- S ----
    "SSM": (0x80, Fmt.S),
    "LPSW": (0x82, Fmt.S),
    "DIAG": (0x83, Fmt.S),
    "WRD": (0x84, Fmt.S),
    "RDD": (0x85, Fmt.S),
    "TS": (0x93, Fmt.S),
}

# Two-byte opcodes (opcode byte, then a second byte that selects the operation).
TWO_BYTE_OPS: dict[str, tuple[int, int, Fmt]] = {
    "SIO": (0x9C, 0x00, Fmt.S),
    "TIO": (0x9D, 0x00, Fmt.S),
    "HIO": (0x9E, 0x00, Fmt.S),
    "TCH": (0x9F, 0x00, Fmt.S),
}

# SS instructions whose length byte holds two independent 4-bit fields L1 and
# L2 (decimal and pack/unpack operations). All other SS instructions carry a
# single 8-bit length applying to both operands.
SS_TWO_LENGTHS = {
    "MVO", "PACK", "UNPK", "ZAP", "CP", "AP", "SP", "MP", "DP", "SRP",
}

MNEMONIC_BY_CODE: dict[int, str] = {}
for _name, (_op, _fmt) in OPCODES.items():
    MNEMONIC_BY_CODE.setdefault(_op, _name)

# Instructions whose second operand is a mask rather than an address/register.
# Used only by the disassembler for readable output.
MASK_OPERAND = {"SPM": 0, "SSM": None, "STNSM": 0, "STOSM": 0}


@dataclass(frozen=True)
class Instruction:
    """A decoded instruction."""

    address: int
    opcode: int
    name: str
    fmt: Fmt
    length: int
    b1: int = 0
    d1: int = 0
    b2: int = 0
    d2: int = 0
    r1: int = 0
    r2: int = 0
    r3: int = 0
    i2: int = 0
    l1: int = 0
    l2: int = 0
    raw: bytes = b""

    @property
    def l(self) -> int:
        """Length field of a one-length SS instruction."""
        return self.l1

    @property
    def hex(self) -> str:
        return self.raw.hex().upper()

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return f"{self.address:06X}  {self.hex:<12} {self.name}"


def _b(mem: bytes | bytearray, addr: int) -> int:
    return mem[addr]


def decode(mem: bytes | bytearray, address: int) -> Instruction:
    """Decode the instruction at *address*.

    Raises ValueError for an unrecognised opcode; callers that are scanning
    (the disassembler, the translator) should catch it and treat the byte as
    data.
    """
    op = _b(mem, address)
    if op in (0x9C, 0x9D, 0x9E, 0x9F):
        second = _b(mem, address + 1)
        for name, (o, s, fmt) in TWO_BYTE_OPS.items():
            if o == op and s == second:
                raw = bytes(mem[address : address + 4])
                return Instruction(address, op, name, fmt, 4, b2=raw[2] >> 4, d2=((raw[2] & 0xF) << 8) | raw[3], raw=raw)
        raise ValueError(f"unknown two-byte opcode {op:02X}{second:02X} at {address:06X}")

    name = MNEMONIC_BY_CODE.get(op)
    if name is None:
        raise ValueError(f"unknown opcode {op:02X} at {address:06X}")
    fmt = OPCODES[name][1]

    if fmt is Fmt.RR:
        raw = bytes(mem[address : address + 2])
        return Instruction(address, op, name, fmt, 2, r1=raw[1] >> 4, r2=raw[1] & 0xF, raw=raw)

    if fmt in (Fmt.RX, Fmt.RS):
        raw = bytes(mem[address : address + 4])
        d2 = ((raw[2] & 0x0F) << 8) | raw[3]
        if fmt is Fmt.RX:
            return Instruction(
                address, op, name, fmt, 4,
                r1=raw[1] >> 4, b2=raw[2] >> 4, d2=d2, raw=raw,
            )
        return Instruction(
            address, op, name, fmt, 4,
            r1=raw[1] >> 4, r3=raw[1] & 0xF, b2=raw[2] >> 4, d2=d2, raw=raw,
        )

    if fmt is Fmt.SI:
        raw = bytes(mem[address : address + 4])
        return Instruction(
            address, op, name, fmt, 4,
            i2=raw[1], b2=raw[2] >> 4, d2=((raw[2] & 0x0F) << 8) | raw[3], raw=raw,
        )

    if fmt is Fmt.SS:
        raw = bytes(mem[address : address + 6])
        if name in SS_TWO_LENGTHS:
            l1, l2 = raw[1] >> 4, raw[1] & 0x0F
        else:
            l1 = l2 = raw[1]
        return Instruction(
            address, op, name, fmt, 6,
            l1=l1, l2=l2,
            b1=raw[2] >> 4, d1=((raw[2] & 0x0F) << 8) | raw[3],
            b2=raw[4] >> 4, d2=((raw[4] & 0x0F) << 8) | raw[5],
            raw=raw,
        )

    # S format
    raw = bytes(mem[address : address + 4])
    return Instruction(
        address, op, name, fmt, 4,
        b2=raw[2] >> 4, d2=((raw[2] & 0x0F) << 8) | raw[3], raw=raw,
    )


def encode(name: str, *fields: int) -> bytes:
    """Encode one instruction. Field order follows the format definition."""
    if name not in OPCODES:
        raise KeyError(name)
    op, fmt = OPCODES[name]

    if fmt is Fmt.RR:
        r1, r2 = fields
        return bytes([op, ((r1 & 0xF) << 4) | (r2 & 0xF)])

    if fmt is Fmt.RX:
        r1, x2, b2, d2 = fields
        return bytes([op, ((r1 & 0xF) << 4) | (x2 & 0xF), ((b2 & 0xF) << 4) | ((d2 >> 8) & 0xF), d2 & 0xFF])

    if fmt is Fmt.RS:
        r1, r3, b2, d2 = fields
        return bytes([op, ((r1 & 0xF) << 4) | (r3 & 0xF), ((b2 & 0xF) << 4) | ((d2 >> 8) & 0xF), d2 & 0xFF])

    if fmt is Fmt.SI:
        i2, b2, d2 = fields
        return bytes([op, i2 & 0xFF, ((b2 & 0xF) << 4) | ((d2 >> 8) & 0xF), d2 & 0xFF])

    if fmt is Fmt.SS:
        if len(fields) == 5:
            # Convenience: one length for both operands.
            l1, b1, d1, b2, d2 = fields
            l2 = l1
        else:
            l1, l2, b1, d1, b2, d2 = fields
        length_byte = (((l1 & 0xF) << 4) | (l2 & 0xF)) if name in SS_TWO_LENGTHS else (l1 & 0xFF)
        return bytes([
            op, length_byte,
            ((b1 & 0xF) << 4) | ((d1 >> 8) & 0xF), d1 & 0xFF,
            ((b2 & 0xF) << 4) | ((d2 >> 8) & 0xF), d2 & 0xFF,
        ])

    b2, d2 = fields
    return bytes([op, 0x00, ((b2 & 0xF) << 4) | ((d2 >> 8) & 0xF), d2 & 0xFF])
