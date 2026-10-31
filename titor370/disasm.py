"""Disassembler for the implemented S/370 subset."""

from __future__ import annotations

from .isa import Fmt, Instruction, decode


def _fmt_dxb(d: int, x: int, b: int) -> str:
    if x and b:
        return f"{d}({x},{b})"
    if b:
        return f"{d}({b})"
    if x:
        return f"{d}({x},0)"
    return str(d)


def format_instruction(ins: Instruction, symbols: dict[int, str] | None = None) -> str:
    name = ins.name
    sym = symbols or {}

    def named(value: int) -> str:
        return sym.get(value, str(value))

    if ins.fmt is Fmt.RR:
        return f"{name:<6} {ins.r1},{ins.r2}"
    if ins.fmt is Fmt.RX:
        x = ins.raw[1] & 0xF
        return f"{name:<6} {ins.r1},{named(ins.d2)}({x},{ins.b2})"
    if ins.fmt is Fmt.RS:
        return f"{name:<6} {ins.r1},{ins.r3},{named(ins.d2)}({ins.b2})"
    if ins.fmt is Fmt.SI:
        return f"{name:<6} {ins.i2},X'{ins.d2:03X}'({ins.b2})"
    if ins.fmt is Fmt.SS:
        return f"{name:<6} X'{ins.d1:03X}'({ins.l + 1},{ins.b1}),X'{ins.d2:03X}'({ins.l + 1},{ins.b2})"
    return f"{name:<6} X'{ins.d2:03X}'({ins.b2})"


def disassemble(
    memory: bytes | bytearray,
    start: int,
    end: int,
    symbols: dict[str, int] | None = None,
) -> list[tuple[int, Instruction | None, str]]:
    rev = {v: k for k, v in (symbols or {}).items()}
    out: list[tuple[int, Instruction | None, str]] = []
    addr = start
    while addr < end:
        try:
            ins = decode(memory, addr)
        except ValueError:
            out.append((addr, None, f"         DC    X'{memory[addr]:02X}'"))
            addr += 1
            continue
        out.append((addr, ins, format_instruction(ins, rev)))
        addr += ins.length
    return out


def listing(rows: list[tuple[int, Instruction | None, str]]) -> str:
    return "\n".join(f"{addr:06X}  {text}" for addr, _ins, text in rows)
