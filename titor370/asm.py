"""A small two-pass assembler for the S/370 subset this emulator implements.

It is deliberately not IBM's assembler: no macros, no conditional assembly, no
DSECTs. What it does support is enough to write real test programs by hand:

    *        comment
    START    EQU   0x1000
             ORG   START
             LA    1,0
    LOOP     L     2,TABLE(1)      load a word from a table
             A     2,=F'0'         -- literals are not supported; use DC
             ...
    TABLE    DC    F'10'
    RESULT   DS    F

Directives: ORG, DC, DS, EQU, END.
Constants:  F'H' halfword, F'D' fullword, C'...' characters, X'..' hex,
            A(label) address, B'...' binary.
Expressions: decimal, 0xHEX, X'..', a symbol, * (location counter), with + and -.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .isa import Fmt, OPCODES, encode


class AsmError(Exception):
    pass


# Standard branch mnemonics, expanded to BC/BCR with an implicit mask.
# Condition codes: 0 equal, 1 low, 2 high, 3 overflow. Mask bit 3-CC selects.
BRANCH_ALIASES: dict[str, int] = {
    "B": 15, "BR": 15,
    "BE": 8, "BNE": 7,
    "BL": 4, "BNL": 11,
    "BH": 2, "BNH": 13,
    "BLE": 12, "BHE": 3,
    "BO": 1, "BNO": 14,
}


def _const_match(text: str) -> tuple[int, str, str] | None:
    """Parse ``[count][type]'body'`` or ``[count]A(expr)``; return (count, type, body)."""
    text = text.strip()
    m = re.fullmatch(r"(\d*)([FHCXAB])'(.*)'", text, re.S)
    if m:
        return int(m.group(1) or 1), m.group(2), m.group(3)
    m = re.fullmatch(r"(\d*)A\((.*)\)", text, re.S)
    if m:
        return int(m.group(1) or 1), "A", m.group(2)
    return None


@dataclass
class Line:
    number: int
    text: str
    label: str | None
    mnemonic: str
    operands: str


def parse_line(text: str, number: int) -> Line | None:
    raw = text.rstrip("\n")
    if not raw.strip() or raw.lstrip().startswith("*"):
        return None
    if raw.lstrip().startswith((".", ";")):
        return None

    label = None
    rest = raw
    # A label is either column 1, or followed by a colon.
    if not rest[0].isspace():
        parts = re.split(r"\s+", rest.strip(), maxsplit=1)
        label = parts[0].rstrip(":")
        rest = parts[1] if len(parts) > 1 else ""
    else:
        stripped = rest.lstrip()
        m = re.match(r"([A-Za-z_.$#@][\w.$#@]*):\s*(.*)", stripped)
        if m:
            label, rest = m.group(1), m.group(2)

    tokens = rest.split(None, 1)
    mnemonic = tokens[0].upper() if tokens else ""
    operands = tokens[1] if len(tokens) > 1 else ""
    # Comments after the operand field start at ';' (or at ' *').
    operands = _strip_comment(operands).strip()
    return Line(number, raw, label, mnemonic, operands)


def _strip_comment(text: str) -> str:
    """Remove a trailing comment without touching quoted constants."""
    quote = None
    for index, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch == ";":
            return text[:index]
    return text


def split_operands(text: str) -> list[str]:
    """Split on commas that are not inside parentheses or quotes."""
    out, depth, quote, current = [], 0, None, []
    for ch in text:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
            current.append(ch)
        elif ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            out.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current or text.strip():
        out.append("".join(current).strip())
    return out


@dataclass
class Chunk:
    address: int
    data: bytes


class Assembler:
    def __init__(self, origin: int = 0x1000):
        self.origin = origin
        self.symbols: dict[str, int] = {}
        self.chunks: list[Chunk] = []
        self.entry: int | None = None
        self.listing: list[str] = []
        self._pass = 0
        # USING support: base register -> value the register holds
        self.using: dict[int, int] = {}
        # Lowest address touched, so the image starts where the program does.
        self.low: int | None = None

    # ------------- expressions -------------

    def eval(self, expr: str, current: int, symbols: dict[str, int] | None = None) -> int:
        expr = expr.strip()
        if not expr:
            raise AsmError("empty expression")
        table = symbols if symbols is not None else self.symbols

        def atom(token: str) -> int:
            token = token.strip()
            if token == "*":
                return current
            m = re.fullmatch(r"X'([0-9A-Fa-f]+)'", token)
            if m:
                return int(m.group(1), 16)
            if re.fullmatch(r"0[xX][0-9A-Fa-f]+", token):
                return int(token, 16)
            if re.fullmatch(r"-?\d+", token):
                return int(token)
            if token in table:
                return table[token]
            raise AsmError(f"undefined symbol {token!r}")

        # Very small expression grammar: atom (('+'|'-') atom)*
        tokens = re.findall(r"X'[0-9A-Fa-f]+'|[A-Za-z_.$#@][\w.$#@]*|0[xX][0-9A-Fa-f]+|\d+|\*|[+-]", expr)
        if not tokens:
            raise AsmError(f"cannot parse expression {expr!r}")
        value = 0
        sign = 1
        expect_atom = True
        for tok in tokens:
            if tok == "+":
                sign = 1
                expect_atom = True
            elif tok == "-":
                sign = -1
                expect_atom = True
            else:
                if not expect_atom:
                    raise AsmError(f"malformed expression {expr!r}")
                value += sign * atom(tok)
                expect_atom = False
        return value

    # ------------- operands -------------

    def reg(self, text: str) -> int:
        text = text.strip()
        if not text:
            return 0
        if not re.fullmatch(r"\d+", text):
            raise AsmError(f"expected a register number, got {text!r}")
        n = int(text)
        if not 0 <= n <= 15:
            raise AsmError(f"register out of range: {n}")
        return n

    def parse_dxbr(self, text: str, current: int, allow_index: bool) -> tuple[int, int, int]:
        """Parse ``D2(X2,B2)`` / ``D2(B2)`` / ``D2`` into (d, x, b)."""
        text = text.strip()
        m = re.fullmatch(r"(.*?)\((.*)\)", text)
        if not m:
            # A bare symbol with an active USING becomes a displacement off that base.
            if self.using and re.fullmatch(r"[A-Za-z_.$#@][\w.$#@]*", text):
                for reg, base_value in self.using.items():
                    disp = self.eval(text, current) - base_value
                    if 0 <= disp <= 0xFFF:
                        return disp, 0, reg
                raise AsmError(f"{text!r} is not reachable from any USING base")
            return self.eval(text, current), 0, 0
        disp, regs = m.group(1).strip(), m.group(2)
        parts = [p.strip() for p in regs.split(",")]
        if allow_index:
            if len(parts) == 1:
                x, b = 0, self.reg(parts[0])
            elif len(parts) == 2:
                x, b = self.reg(parts[0]), self.reg(parts[1])
            else:
                raise AsmError(f"bad register list {regs!r}")
        else:
            if len(parts) != 1:
                raise AsmError(f"bad register list {regs!r}")
            x, b = 0, self.reg(parts[0])
        d = self.eval(disp, current) if disp else 0
        return d, x, b

    def parse_len_dxb(self, text: str, current: int) -> tuple[int, int, int, int]:
        """Parse ``D1(L1,B1)`` into (d, l, b). L is 1-based: L1=L-1."""
        text = text.strip()
        m = re.fullmatch(r"(.*?)\((.*)\)", text)
        if not m:
            raise AsmError(f"expected D(L,B), got {text!r}")
        disp, rest = m.group(1).strip(), m.group(2)
        parts = [p.strip() for p in rest.split(",")]

        if len(parts) == 1:
            # "SYM(L)" -- base comes from the active USING, if any.
            length = parts[0]
            value = self.eval(disp, current) if disp else 0
            for reg, base_value in self.using.items():
                off = value - base_value
                if 0 <= off <= 0xFFF:
                    return off, self.eval(length, current) - 1, reg
            if not disp:
                raise AsmError(f"no USING base for {text!r}")
            return value, self.eval(length, current) - 1, 0
        if len(parts) == 3:
            length, _x, base = parts
        elif len(parts) == 2:
            length, base = parts
        else:
            raise AsmError(f"expected D(L,B), got {text!r}")
        d = self.eval(disp, current) if disp else 0
        n = self.eval(length, current)
        if not 1 <= n <= 256:
            raise AsmError(f"length must be 1..256, got {n}")
        return d, n - 1, self.reg(base)

    # ------------- directives -------------

    def dc(self, operands: str, current: int) -> bytes:
        out = bytearray()
        for item in split_operands(operands):
            m = re.fullmatch(r"(\d*)([FHCXAB])'(.*)'", item.strip(), re.S)
            if not m:
                raise AsmError(f"bad constant {item!r}")
            repeat = int(m.group(1) or 1)
            kind, body = m.group(2), m.group(3)
            for _ in range(repeat):
                out += self._one_constant(kind, body, current)
        return bytes(out)

    def _one_constant(self, kind: str, body: str, current: int) -> bytes:
        out = bytearray()
        if True:
            if kind == "F":
                out += (self.eval(body, current) & 0xFFFFFFFF).to_bytes(4, "big")
            elif kind == "H":
                out += (self.eval(body, current) & 0xFFFF).to_bytes(2, "big")
            elif kind == "C":
                out += body.encode("cp037")  # EBCDIC, as S/370 would
            elif kind == "X":
                out += bytes.fromhex(re.sub(r"\s+", "", body))
            elif kind == "B":
                bits = re.sub(r"\s+", "", body)
                value = int(bits, 2) if bits else 0
                width = max(1, (len(bits) + 7) // 8)
                out += value.to_bytes(width, "big")
            elif kind == "A":
                out += (self.eval(body, current) & 0xFFFFFF).to_bytes(4, "big")
        return bytes(out)

    def ds(self, operands: str, current: int) -> int:
        m = re.fullmatch(r"\s*(\d*)\s*([FHDCX]?)\s*", operands)
        if not m:
            raise AsmError(f"bad DS operand {operands!r}")
        count = int(m.group(1) or 1)
        unit = {"F": 4, "H": 2, "D": 8, "C": 1, "X": 1, "": 1}[m.group(2)]
        return count * unit

    # ------------- assembly -------------

    def dc_size(self, operands: str) -> int:
        """Size of a DC in bytes, without needing operand values (pass 1)."""
        total = 0
        for item in split_operands(operands):
            parsed = _const_match(item)
            if not parsed:
                raise AsmError(f"bad constant {item!r}")
            count, kind, body = parsed
            total += count * self._one_size(kind, body)
        return total

    @staticmethod
    def _one_size(kind: str, body: str) -> int:
        if kind in ("F", "A"):
            return 4
        if kind == "H":
            return 2
        if kind == "C":
            return len(body.encode("cp037"))
        if kind == "X":
            return len(re.sub(r"\s+", "", body)) // 2
        if kind == "A":
            return 4
        if kind == "B":
            bits = re.sub(r"\s+", "", body)
            return max(1, (len(bits) + 7) // 8)
        raise AsmError(f"unsupported constant type {kind!r}")

    def dc(self, operands: str, current: int) -> bytes:
        out = bytearray()
        for item in split_operands(operands):
            parsed = _const_match(item)
            if not parsed:
                raise AsmError(f"bad constant {item!r}")
            repeat, kind, body = parsed
            out += self._one_constant(kind, body, current) * repeat
        return bytes(out)

    def _one_constant(self, kind: str, body: str, current: int) -> bytes:
        if kind == "A":
            return (self.eval(body, current) & 0xFFFFFF).to_bytes(4, "big")
        if kind == "F":
            return (self.eval(body, current) & 0xFFFFFFFF).to_bytes(4, "big")
        if kind == "H":
            return (self.eval(body, current) & 0xFFFF).to_bytes(2, "big")
        if kind == "C":
            return body.encode("cp037")  # EBCDIC, as S/370 would store it
        if kind == "X":
            return bytes.fromhex(re.sub(r"\s+", "", body))
        if kind == "B":
            bits = re.sub(r"\s+", "", body)
            value = int(bits, 2) if bits else 0
            width = max(1, (len(bits) + 7) // 8)
            return value.to_bytes(width, "big")
        if kind == "A":
            return (self.eval(body, current) & 0xFFFFFF).to_bytes(4, "big")
        raise AsmError(f"unsupported constant type {kind!r}")

    def assemble(self, source: str) -> dict:
        lines = [parse_line(t, n + 1) for n, t in enumerate(source.splitlines())]
        lines = [ln for ln in lines if ln is not None]

        # ---- pass 1: symbols and addresses ----
        current = self.origin
        for ln in lines:
            if ln.label:
                if ln.mnemonic == "EQU":
                    self.symbols[ln.label] = self.eval(ln.operands, current) if ln.operands else current
                else:
                    self.symbols[ln.label] = current
            if ln.mnemonic in ("EQU", ""):
                continue
            if ln.mnemonic == "ORG":
                current = self.eval(ln.operands, current)
                continue
            if ln.mnemonic == "USING":
                base_sym, _, reg = ln.operands.partition(",")
                self.using[self.reg(reg)] = self.eval(base_sym.strip(), current)
                continue
            if ln.mnemonic == "DROP":
                for r in [self.reg(x) for x in ln.operands.split(",") if x.strip()]:
                    self.using.pop(r, None)
                continue
            if ln.mnemonic == "END":
                break
            if ln.mnemonic == "DC":
                current += self.dc_size(ln.operands)
                continue
            if ln.mnemonic == "DS":
                current += self.ds(ln.operands, current)
                continue
            if ln.mnemonic in BRANCH_ALIASES:
                current += 2 if ln.mnemonic == "BR" else 4
                continue
            if ln.mnemonic not in OPCODES:
                raise AsmError(f"line {ln.number}: unknown mnemonic {ln.mnemonic!r}")
            current += {Fmt.RR: 2, Fmt.SS: 6}.get(OPCODES[ln.mnemonic][1], 4)

        if "START" in self.symbols:
            self.entry = self.symbols["START"]
        elif "BEGIN" in self.symbols:
            self.entry = self.symbols["BEGIN"]

        # ---- pass 2: encode ----
        current = self.origin
        for ln in lines:
            if ln.mnemonic in ("EQU", ""):
                continue
            if ln.mnemonic == "ORG":
                current = self.eval(ln.operands, current)
                continue
            if ln.mnemonic == "USING":
                base_sym, _, reg = ln.operands.partition(",")
                self.using[self.reg(reg)] = self.eval(base_sym.strip(), current)
                continue
            if ln.mnemonic == "DROP":
                for r in [self.reg(x) for x in ln.operands.split(",") if x.strip()]:
                    self.using.pop(r, None)
                continue
            if ln.mnemonic == "END":
                if ln.operands:
                    self.entry = self.eval(ln.operands, current)
                break
            self.low = current if self.low is None else min(self.low, current)
            if ln.mnemonic == "DC":
                data = self.dc(ln.operands, current)
                self.chunks.append(Chunk(current, data))
                current += len(data)
                continue
            if ln.mnemonic == "DS":
                current += self.ds(ln.operands, current)
                continue
            data = self.encode_instruction(ln, current)
            self.chunks.append(Chunk(current, data))
            self.listing.append(
                f"{current:06X}  {data.hex().upper():<12} {ln.mnemonic:<6} {ln.operands}"
            )
            current += len(data)

        image = bytearray()
        # The image starts at the origin so that a program beginning with DS or
        # with leading gaps still lands at the right addresses.
        candidates = [c.address for c in self.chunks]
        if self.low is not None:
            candidates.append(self.low)
        base = min(candidates) if candidates else self.origin
        for chunk in sorted(self.chunks, key=lambda c: c.address):
            offset = chunk.address - base
            if offset > len(image):
                image += bytes(offset - len(image))
            image += chunk.data
        return {
            "image": bytes(image),
            "base": base,
            "entry": self.entry if self.entry is not None else base,
            "end": base + len(image),
            "symbols": dict(self.symbols),
            "listing": list(self.listing),
        }

    def encode_instruction(self, ln: Line, current: int) -> bytes:
        name = ln.mnemonic
        ops = split_operands(ln.operands)

        if name in BRANCH_ALIASES:
            mask = BRANCH_ALIASES[name]
            if name == "BR":
                return encode("BCR", mask, self.reg(ops[0]) if ops else 14)
            d, x, b = self.parse_dxbr(ops[0] if ops else "0", current, True)
            return encode("BC", mask, x, b, d)

        fmt = OPCODES[name][1]

        if fmt is Fmt.RR:
            r1 = self.reg(ops[0]) if len(ops) > 0 else 0
            r2 = self.reg(ops[1]) if len(ops) > 1 else 0
            # Allow a bare number for BCR 15,14 style masks.
            return encode(name, r1, r2)

        if fmt is Fmt.RX:
            r1 = self.reg(ops[0]) if ops else 0
            d, x, b = self.parse_dxbr(ops[1] if len(ops) > 1 else "0", current, True)
            return encode(name, r1, x, b, d)

        if fmt is Fmt.RS:
            r1 = self.reg(ops[0]) if ops else 0
            if name in ("ICM", "STCM", "CLM"):
                # The second field is a 4-bit byte mask, not a register.
                r3 = self.eval(ops[1], current) & 0xF if len(ops) > 1 else 0
            else:
                r3 = self.reg(ops[1]) if len(ops) > 1 else 0
            d, _x, b = self.parse_dxbr(ops[2] if len(ops) > 2 else "0", current, False)
            return encode(name, r1, r3, b, d)

        if fmt is Fmt.SI:
            imm = self.eval(ops[0], current) if ops else 0
            if not 0 <= imm <= 0xFF:
                raise AsmError(f"immediate out of range: {imm}")
            d, _x, b = self.parse_dxbr(ops[1] if len(ops) > 1 else "0", current, False)
            return encode(name, imm, b, d)

        if fmt is Fmt.SS:
            # Operands may be written D(L,B), D(L), or a bare symbol whose
            # length is inherited from the other operand (default 1).
            if "(" in ops[0]:
                d1, l1, b1 = self.parse_len_dxb(ops[0], current)
            else:
                d1, _x, b1 = self.parse_dxbr(ops[0], current, False)
                l1 = 0
            if len(ops) > 1 and "(" in ops[1]:
                d2, l2, b2 = self.parse_len_dxb(ops[1], current)
            else:
                d2, _x, b2 = self.parse_dxbr(ops[1] if len(ops) > 1 else "0", current, False)
                l2 = l1
            if l1 == 0:
                l1 = l2
            if l1 != l2:
                raise AsmError("SS operands must have equal lengths")
            return encode(name, l1, b1, d1, b2, d2)

        # S format
        d, _x, b = self.parse_dxbr(ops[0] if ops else "0", current, False)
        return encode(name, b, d)


def assemble(source: str, origin: int = 0x1000) -> dict:
    return Assembler(origin).assemble(source)
