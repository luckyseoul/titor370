"""A System/370 CPU: registers, PSW, 24-bit real storage, instruction execution.

Scope and honesty
-----------------
This is a *real* S/370 interpreter for the unprivileged instruction set that
ordinary application code uses. It is not a system: there is no DAT, no channel
subsystem, no storage keys, and no supervisor. Privileged instructions raise
PrivilegedOperation rather than pretending to work.

What is implemented is listed in IMPLEMENTED; what is deliberately not is listed
in UNIMPLEMENTED and raises OperationException. Both lists are checked by tests,
so the README cannot drift from the code.

Semantics follow GA22-7000-4 (September 1975). Where a rule is subtle --
logical subtraction's carry, shift overflow, MVC overlap, BXH's unconditional
increment -- the comment cites the rule being applied.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .isa import Fmt, Instruction, decode

MEMSIZE = 1 << 24  # 16 MiB, 24-bit real storage
MASK24 = 0xFFFFFF
MASK32 = 0xFFFFFFFF


# --------------------------------------------------------------------------
# Interruptions
# --------------------------------------------------------------------------


class S370Error(Exception):
    """Base class for everything this CPU raises."""


class ProgramCheck(S370Error):
    """A program interruption. *code* is the S/370 program-interruption code."""

    NAMES = {
        0x01: "operation",
        0x02: "privileged-operation",
        0x03: "execute",
        0x04: "protection",
        0x05: "addressing",
        0x06: "specification",
        0x07: "data",
        0x08: "fixed-point-overflow",
        0x09: "fixed-point-divide",
        0x0A: "decimal-overflow",
        0x0B: "decimal-divide",
        0x0C: "exponent-overflow",
        0x0D: "exponent-underflow",
        0x0F: "segment-translation",
        0x10: "page-translation",
    }

    def __init__(self, code: int, detail: str = "", address: int | None = None):
        self.code = code
        self.detail = detail
        self.address = address
        label = self.NAMES.get(code, f"code-{code:02X}")
        where = f" at {address:06X}" if address is not None else ""
        super().__init__(f"{label}{where}" + (f": {detail}" if detail else ""))


class OperationException(ProgramCheck):
    def __init__(self, detail: str = "", address: int | None = None):
        super().__init__(0x01, detail, address)


class PrivilegedOperation(ProgramCheck):
    def __init__(self, detail: str = "", address: int | None = None):
        super().__init__(0x02, detail, address)


class SpecificationException(ProgramCheck):
    def __init__(self, detail: str = "", address: int | None = None):
        super().__init__(0x06, detail, address)


class AddressingException(ProgramCheck):
    def __init__(self, detail: str = "", address: int | None = None):
        super().__init__(0x05, detail, address)


class FixedPointOverflow(ProgramCheck):
    def __init__(self, detail: str = "", address: int | None = None):
        super().__init__(0x08, detail, address)


class FixedPointDivide(ProgramCheck):
    def __init__(self, detail: str = "", address: int | None = None):
        super().__init__(0x09, detail, address)


class SupervisorCall(S370Error):
    def __init__(self, code: int, address: int):
        self.code = code
        self.address = address
        super().__init__(f"SVC {code} at {address:06X}")


class Halt(S370Error):
    """Raised by the DIAG 'halt' convention used by this project's test programs."""


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def s32(v: int) -> int:
    """Reinterpret a 32-bit pattern as signed."""
    v &= MASK32
    return v - 0x1_0000_0000 if v & 0x8000_0000 else v


def u32(v: int) -> int:
    return v & MASK32


def s64(v: int) -> int:
    v &= 0xFFFF_FFFF_FFFF_FFFF
    return v - 0x1_0000_0000_0000_0000 if v & 0x8000_0000_0000_0000 else v


def cc_for(value: int) -> int:
    """Condition code for a signed 32-bit result: 0 zero, 1 negative, 2 positive."""
    v = s32(value)
    return 0 if v == 0 else (1 if v < 0 else 2)


def wrap24(addr: int) -> int:
    return addr & MASK24


# --------------------------------------------------------------------------
# PSW
# --------------------------------------------------------------------------


@dataclass
class PSW:
    """EC-mode PSW, as defined in GA22-7000-4 Appendix D.

    Only the fields this emulator models are named; the rest are carried so that
    a PSW dumped from real storage round-trips.
    """

    cc: int = 0
    pmask: int = 0
    wait: bool = False
    problem: bool = True
    dat: bool = False
    ia: int = 0

    def to_word(self) -> int:
        w = 0
        if self.dat:
            w |= 1 << 6
        w |= 1 << 12  # EC mode
        if self.wait:
            w |= 1 << 13
        if self.problem:
            w |= 1 << 14
        w |= (self.cc & 0x3) << 30      # bits 34-35 of the 64-bit PSW
        w |= (self.pmask & 0xF) << 28   # bits 36-39
        # Instruction address lives in the low word (bits 40-63) for our purposes
        return (w << 32) | (self.ia & MASK32)

    @classmethod
    def from_word(cls, value: int) -> "PSW":
        hi = (value >> 32) & MASK32
        lo = value & MASK32
        return cls(
            cc=(hi >> 30) & 0x3,
            pmask=(hi >> 28) & 0xF,
            wait=bool(hi & (1 << 13)),
            problem=bool(hi & (1 << 14)),
            dat=bool(hi & (1 << 6)),
            ia=lo,
        )


# --------------------------------------------------------------------------
# CPU
# --------------------------------------------------------------------------

IMPLEMENTED = """
AR SR ALR SLR LR LTR LNR LPR LCR NR OR XR CR CLR MR DR
AP SP MP DP ZAP CP MVO SRP ED EDMK
BALR BCTR BCR SVC MVCL CLCL
L ST LA LH STH IC STC A S AL SL N O X C CL M D AH SH CH MH CVD CVB
EX BAL BCT BC LRA
SLL SRL SLA SRA SLDL SRDL SLDA SRDA STM LM BXH BXLE CS CDS CLM STCM ICM
TM MVI NI OI XI CLI
MVC MVN MVZ NC OC XC CLC TR TRT PACK UNPK
TS LPSW SSM
DIAG
""".split()

UNIMPLEMENTED = """
SPM SSK ISK WRD RDD STNSM STOSM SIO TIO HIO TCH
LRER LRDR LTER LTDR LCER LCDR
AD ADR AE AER AW AWR AU AUR SD SDR SE SER SW SWR SU SUR
MD MDR ME MER MXD MXDR MXR DD DDR DE DER
HDR HER LD LDR LE LER STE STD
LPDR LNDR LPER LNER LTER LNER
CD CDR CE CER
""".split()


@dataclass
class CPU:
    memory: bytearray = field(default_factory=lambda: bytearray(MEMSIZE))
    r: list[int] = field(default_factory=lambda: [0] * 16)
    psw: PSW = field(default_factory=PSW)
    steps: int = 0
    max_steps: int = 5_000_000
    instructions: dict[str, int] = field(default_factory=dict)
    trace: list[str] | None = None
    halted: bool = False

    # ---------------- storage helpers ----------------

    def _check(self, addr: int, length: int, where: int | None = None) -> int:
        a = wrap24(addr)
        if length and a + length > MEMSIZE:
            raise AddressingException(f"{a:06X}+{length}", where)
        return a

    def fetch_byte(self, addr: int, where: int | None = None) -> int:
        return self.memory[self._check(addr, 1, where)]

    def fetch_u16(self, addr: int, where: int | None = None) -> int:
        a = self._check(addr, 2, where)
        return (self.memory[a] << 8) | self.memory[a + 1]

    def fetch_u32(self, addr: int, where: int | None = None) -> int:
        a = self._check(addr, 4, where)
        return (
            (self.memory[a] << 24)
            | (self.memory[a + 1] << 16)
            | (self.memory[a + 2] << 8)
            | self.memory[a + 3]
        )

    def store_byte(self, addr: int, value: int, where: int | None = None) -> None:
        self.memory[self._check(addr, 1, where)] = value & 0xFF

    def store_u16(self, addr: int, value: int, where: int | None = None) -> None:
        a = self._check(addr, 2, where)
        self.memory[a] = (value >> 8) & 0xFF
        self.memory[a + 1] = value & 0xFF

    def store_u32(self, addr: int, value: int, where: int | None = None) -> None:
        a = self._check(addr, 4, where)
        value &= MASK32
        self.memory[a] = (value >> 24) & 0xFF
        self.memory[a + 1] = (value >> 16) & 0xFF
        self.memory[a + 2] = (value >> 8) & 0xFF
        self.memory[a + 3] = value & 0xFF

    def read(self, addr: int, length: int) -> bytes:
        a = self._check(addr, length)
        return bytes(self.memory[a : a + length])

    def write(self, addr: int, data: bytes) -> None:
        a = self._check(addr, len(data))
        self.memory[a : a + len(data)] = data

    # ---------------- addressing ----------------

    def addr(self, b: int, d: int, x: int = 0, where: int | None = None) -> int:
        """Effective address: D + (X) + (B), masked to 24 bits.

        A base or index field of zero means "not used" -- it does *not* name
        register 0 (GA22-7000-4, "Addressing").
        """
        value = d
        if x:
            value += self.r[x] & MASK32
        if b:
            value += self.r[b] & MASK32
        return wrap24(value)

    # ---------------- condition code ----------------

    @property
    def cc(self) -> int:
        return self.psw.cc

    @cc.setter
    def cc(self, value: int) -> None:
        self.psw.cc = value & 0x3

    # ---------------- run loop ----------------

    def run(self, start: int | None = None, max_steps: int | None = None) -> None:
        if start is not None:
            self.psw.ia = wrap24(start)
        limit = max_steps if max_steps is not None else self.max_steps
        executed = 0
        while not self.halted:
            if executed >= limit:
                raise S370Error(f"step limit {limit} reached at {self.psw.ia:06X}")
            try:
                self.step()
            except Halt:
                break
            executed += 1

    def step(self) -> Instruction:
        ia = wrap24(self.psw.ia)
        try:
            ins = decode(self.memory, ia)
        except ValueError as exc:
            raise OperationException(str(exc), ia) from None
        if self.trace is not None:
            self.trace.append(f"{ia:06X}  {ins.hex:<12} {ins.name}")
        self.instructions[ins.name] = self.instructions.get(ins.name, 0) + 1
        self.steps += 1
        self.execute(ins)
        return ins

    def execute(self, ins: Instruction) -> None:
        handler = getattr(self, f"op_{ins.name}", None)
        if handler is None:
            raise OperationException(f"{ins.name} not implemented", ins.address)
        handler(ins)

    # ---------------- RR ----------------

    def op_LR(self, i: Instruction) -> None:
        self.r[i.r1] = self.r[i.r2]
        self.psw.ia = wrap24(i.address + i.length)

    def op_LTR(self, i: Instruction) -> None:
        self.r[i.r1] = self.r[i.r2]
        self.cc = cc_for(self.r[i.r1])
        self.psw.ia = wrap24(i.address + i.length)

    def op_LNR(self, i: Instruction) -> None:
        """LOAD NEGATIVE: "The two's complement of the absolute value of the
        second operand is placed in the first-operand location. The operation
        complements positive numbers; negative numbers remain unchanged"
        (GA22-7000-4, p. 132). CC: 0 zero, 1 less than zero."""
        value = s32(self.r[i.r2])
        self.r[i.r1] = u32(-abs(value))
        self.cc = 0 if self.r[i.r1] == 0 else 1
        self.psw.ia = wrap24(i.address + i.length)

    def op_LPR(self, i: Instruction) -> None:
        v = s32(self.r[i.r2])
        if v == -(2**31):
            self.r[i.r1] = u32(v)
            self.cc = 3
        else:
            self.r[i.r1] = u32(abs(v))
            self.cc = 0 if v == 0 else 2
        self.psw.ia = wrap24(i.address + i.length)

    def op_LCR(self, i: Instruction) -> None:
        v = s32(self.r[i.r2])
        if v == -(2**31):
            self.r[i.r1] = u32(v)
            self.cc = 3
        else:
            self.r[i.r1] = u32(-v)
            self.cc = cc_for(self.r[i.r1])
        self.psw.ia = wrap24(i.address + i.length)

    def _set_add_cc(self, result: int, where: int) -> int:
        if -2**31 <= result < 2**31:
            self.cc = cc_for(result)
            return u32(result)
        # Overflow takes precedence over the sign (GA22-7000-4), and causes a
        # program interruption only when the fixed-point overflow mask bit is
        # one. Program mask bit 0 is decimal overflow, bit 1 fixed-point
        # overflow, bit 2 decimal divide, bit 3 exponent underflow.
        self.cc = 3
        if self.psw.pmask & 0x4:
            raise FixedPointOverflow("fixed-point overflow", where)
        return u32(result)

    def op_AR(self, i: Instruction) -> None:
        self.r[i.r1] = self._set_add_cc(s32(self.r[i.r1]) + s32(self.r[i.r2]), i.address)
        self.psw.ia = wrap24(i.address + i.length)

    def op_SR(self, i: Instruction) -> None:
        self.r[i.r1] = self._set_add_cc(s32(self.r[i.r1]) - s32(self.r[i.r2]), i.address)
        self.psw.ia = wrap24(i.address + i.length)

    def _logical_add_cc(self, result: int, carry: bool) -> None:
        # ADD LOGICAL: 0 zero/no-carry, 1 nonzero/no-carry, 2 zero/carry, 3 nonzero/carry
        nonzero = (result & MASK32) != 0
        self.cc = (2 if carry else 0) + (1 if nonzero else 0)

    def op_ALR(self, i: Instruction) -> None:
        s = (self.r[i.r1] & MASK32) + (self.r[i.r2] & MASK32)
        self.r[i.r1] = s & MASK32
        self._logical_add_cc(s, s > MASK32)
        self.psw.ia = wrap24(i.address + i.length)

    def _subtract_logical_cc(self, a: int, b: int, result: int) -> None:
        """SUBTRACT LOGICAL condition code (GA22-7000-4, p. 144).

        The carry out of the sign position is recorded: 1 + (carry ? 2 : 0)
        plus 1 if the difference is nonzero. "A zero difference cannot be
        obtained without a carry out of the sign position", so CC 0 never
        occurs.
        """
        carry = a >= b            # carry out of the sign position
        self.cc = (2 if carry else 0) | (1 if result & MASK32 else 0)

    def op_SLR(self, i: Instruction) -> None:
        a, b = self.r[i.r1] & MASK32, self.r[i.r2] & MASK32
        result = (a - b) & MASK32
        self.r[i.r1] = result
        self._subtract_logical_cc(a, b, result)
        self.psw.ia = wrap24(i.address + i.length)

    def op_NR(self, i: Instruction) -> None:
        self.r[i.r1] &= self.r[i.r2]
        self.cc = 0 if self.r[i.r1] == 0 else 1
        self.psw.ia = wrap24(i.address + i.length)

    def op_OR(self, i: Instruction) -> None:
        self.r[i.r1] |= self.r[i.r2]
        self.cc = 0 if self.r[i.r1] == 0 else 1
        self.psw.ia = wrap24(i.address + i.length)

    def op_XR(self, i: Instruction) -> None:
        self.r[i.r1] ^= self.r[i.r2]
        self.cc = 0 if self.r[i.r1] == 0 else 1
        self.psw.ia = wrap24(i.address + i.length)

    def op_CR(self, i: Instruction) -> None:
        a, b = s32(self.r[i.r1]), s32(self.r[i.r2])
        self.cc = 0 if a == b else (1 if a < b else 2)
        self.psw.ia = wrap24(i.address + i.length)

    def op_CLR(self, i: Instruction) -> None:
        a, b = self.r[i.r1] & MASK32, self.r[i.r2] & MASK32
        self.cc = 0 if a == b else (1 if a < b else 2)
        self.psw.ia = wrap24(i.address + i.length)

    def op_MR(self, i: Instruction) -> None:
        if i.r1 % 2:
            raise SpecificationException("MR odd first operand", i.address)
        # "The multiplicand is taken from the odd register of the pair. The
        # contents of the even-numbered register replaced by the product are
        # ignored" (GA22-7000-4, p. 136).
        product = s32(self.r[i.r1 + 1]) * s32(self.r[i.r2])
        self.r[i.r1] = (product >> 32) & MASK32
        self.r[i.r1 + 1] = product & MASK32
        self.psw.ia = wrap24(i.address + i.length)

    def op_DR(self, i: Instruction) -> None:
        if i.r1 % 2:
            raise SpecificationException("DR odd first operand", i.address)
        divisor = s32(self.r[i.r2])
        dividend = (self.r[i.r1] << 32) | self.r[i.r1 + 1]
        dividend = s64(dividend)
        if divisor == 0:
            raise FixedPointDivide("division by zero", i.address)
        quotient, remainder = _trunc_divmod(dividend, divisor)
        if not (-2**31 <= quotient < 2**31):
            raise FixedPointDivide("quotient overflow", i.address)
        # "A 32-bit signed remainder and a 32-bit signed quotient replace the
        # dividend in the even-numbered and odd-numbered registers,
        # respectively" (GA22-7000-4, p. 128).
        self.r[i.r1] = remainder & MASK32
        self.r[i.r1 + 1] = quotient & MASK32
        self.psw.ia = wrap24(i.address + i.length)

    def _link_word(self, i: Instruction) -> int:
        """Link information: ILC | CC | program mask | instruction address
        (GA22-7000-4, p. 121). ILC is 1 for a two-byte instruction and 2 for
        a four-byte one."""
        ilc = 1 if i.length == 2 else 2
        return ((ilc << 30) | ((self.cc & 0x3) << 28)
                | ((self.psw.pmask & 0xF) << 24)
                | ((i.address + i.length) & MASK24))

    def op_BALR(self, i: Instruction) -> None:
        target = self.r[i.r2] & MASK24
        if i.r1:
            self.r[i.r1] = self._link_word(i)
        if i.r2:
            self.psw.ia = target
        else:
            self.psw.ia = wrap24(i.address + i.length)

    def op_BCTR(self, i: Instruction) -> None:
        # "The branch address is computed before the counting operation."
        target = self.r[i.r2] & MASK24
        self.r[i.r1] = (self.r[i.r1] - 1) & MASK32
        if self.r[i.r1] != 0 and i.r2:
            self.psw.ia = target
        else:
            self.psw.ia = wrap24(i.address + i.length)

    def op_BCR(self, i: Instruction) -> None:
        # The R1 field of BCR is the M1 mask itself, not a register number
        # (GA22-7000-4, p. 121: "The M1 field is used as a four-bit mask").
        mask = i.r1 & 0xF
        if mask & (1 << (3 - self.cc)) and i.r2:
            self.psw.ia = self.r[i.r2] & MASK24
        else:
            self.psw.ia = wrap24(i.address + i.length)

    def op_SVC(self, i: Instruction) -> None:
        # The interruption code is the I2 field (GA22-7000-4, SUPERVISOR CALL).
        raise SupervisorCall(i.raw[1], i.address)

    def op_MVCL(self, i: Instruction) -> None:
        """MOVE LONG.

        R1 and R2 each designate an even-odd pair. The leftmost byte of each
        operand is addressed by bits 8-31 of R1/R2; the counts are in bits 8-31
        of R1+1/R2+1; the padding character is in bits 0-7 of R2+1
        (GA22-7000-4, p. 133).
        """
        if i.r1 % 2 or i.r2 % 2:
            raise SpecificationException("MVCL odd register", i.address)
        d, src = self.r[i.r1], self.r[i.r2]
        daddr, saddr = d & MASK24, src & MASK24
        dlen = self.r[i.r1 + 1] & MASK24
        slen = self.r[i.r2 + 1] & MASK24
        pad = (self.r[i.r2 + 1] >> 24) & 0xFF

        n = min(dlen, slen)
        # "No movement performed because of destructive overlap": the first
        # operand starts to the right of the second but inside its field.
        if n and daddr > saddr and daddr < saddr + n:
            self.cc = 3
            self.psw.ia = wrap24(i.address + i.length)
            return

        if n:
            chunk = bytes(self.memory[saddr : saddr + n])
            self.memory[daddr : daddr + n] = chunk
        if slen < dlen:
            self.memory[daddr + slen : daddr + dlen] = bytes([pad]) * (dlen - slen)

        self.cc = 0 if dlen == slen else (1 if dlen < slen else 2)
        # "The high-order byte of registers R1 and R2 is set to zero; the
        # contents of the high-order byte of registers R1+1 and R2+1 remain
        # unchanged" (p. 134).
        self.r[i.r1] = (daddr + n) & MASK24
        self.r[i.r2] = (saddr + n) & MASK24
        self.r[i.r1 + 1] = (self.r[i.r1 + 1] & 0xFF000000) | ((dlen - n) & MASK24)
        self.r[i.r2 + 1] = (self.r[i.r2 + 1] & 0xFF000000) | ((slen - n) & MASK24)
        self.psw.ia = wrap24(i.address + i.length)

    def op_CLCL(self, i: Instruction) -> None:
        """COMPARE LOGICAL LONG: same register layout as MVCL, and "the shorter
        operand is considered extended with the padding character" (p. 126)."""
        if i.r1 % 2 or i.r2 % 2:
            raise SpecificationException("CLCL odd register", i.address)
        a, b = self.r[i.r1] & MASK24, self.r[i.r2] & MASK24
        alen = self.r[i.r1 + 1] & MASK24
        blen = self.r[i.r2 + 1] & MASK24
        pad = (self.r[i.r2 + 1] >> 24) & 0xFF

        n = min(alen, blen)
        left = bytes(self.memory[a : a + n])
        right = bytes(self.memory[b : b + n])
        self.cc = 0
        for x, y in zip(left, right):
            if x != y:
                self.cc = 1 if x < y else 2
                break
        if self.cc == 0 and alen != blen:
            # The shorter operand is padded; compare the pad against the rest.
            longer_from, longer_len = (b, blen) if alen < blen else (a, alen)
            for k in range(n, longer_len):
                byte = self.memory[longer_from + k]
                if byte != pad:
                    first_is_low = (byte > pad) if alen < blen else (byte < pad)
                    self.cc = 1 if first_is_low else 2
                    break

        self.r[i.r1] = (a + n) & MASK24
        self.r[i.r2] = (b + n) & MASK24
        self.r[i.r1 + 1] = (self.r[i.r1 + 1] & 0xFF000000) | ((alen - n) & MASK24)
        self.r[i.r2 + 1] = (self.r[i.r2 + 1] & 0xFF000000) | ((blen - n) & MASK24)
        self.psw.ia = wrap24(i.address + i.length)

    # ---------------- RX ----------------

    def _rx_addr(self, i: Instruction) -> int:
        x = i.raw[1] & 0xF
        return self.addr(i.b2, i.d2, x, i.address)

    def op_L(self, i: Instruction) -> None:
        self.r[i.r1] = self.fetch_u32(self._rx_addr(i), i.address)
        self.psw.ia = wrap24(i.address + i.length)

    def op_ST(self, i: Instruction) -> None:
        self.store_u32(self._rx_addr(i), self.r[i.r1], i.address)
        self.psw.ia = wrap24(i.address + i.length)

    def op_LA(self, i: Instruction) -> None:
        self.r[i.r1] = self._rx_addr(i)
        self.psw.ia = wrap24(i.address + i.length)

    def op_LH(self, i: Instruction) -> None:
        v = self.fetch_u16(self._rx_addr(i), i.address)
        self.r[i.r1] = u32(v - 0x10000 if v & 0x8000 else v)
        self.psw.ia = wrap24(i.address + i.length)

    def op_STH(self, i: Instruction) -> None:
        self.store_u16(self._rx_addr(i), self.r[i.r1] & 0xFFFF, i.address)
        self.psw.ia = wrap24(i.address + i.length)

    def op_IC(self, i: Instruction) -> None:
        self.r[i.r1] = (self.r[i.r1] & 0xFFFFFF00) | self.fetch_byte(self._rx_addr(i), i.address)
        self.psw.ia = wrap24(i.address + i.length)

    def op_STC(self, i: Instruction) -> None:
        self.store_byte(self._rx_addr(i), self.r[i.r1] & 0xFF, i.address)
        self.psw.ia = wrap24(i.address + i.length)

    def op_A(self, i: Instruction) -> None:
        self.r[i.r1] = self._set_add_cc(s32(self.r[i.r1]) + s32(self.fetch_u32(self._rx_addr(i), i.address)), i.address)
        self.psw.ia = wrap24(i.address + i.length)

    def op_S(self, i: Instruction) -> None:
        self.r[i.r1] = self._set_add_cc(s32(self.r[i.r1]) - s32(self.fetch_u32(self._rx_addr(i), i.address)), i.address)
        self.psw.ia = wrap24(i.address + i.length)

    def op_AL(self, i: Instruction) -> None:
        s = (self.r[i.r1] & MASK32) + self.fetch_u32(self._rx_addr(i), i.address)
        self.r[i.r1] = s & MASK32
        self._logical_add_cc(s, s > MASK32)
        self.psw.ia = wrap24(i.address + i.length)

    def op_SL(self, i: Instruction) -> None:
        b = self.fetch_u32(self._rx_addr(i), i.address)
        a = self.r[i.r1] & MASK32
        result = (a - b) & MASK32
        self.r[i.r1] = result
        self._subtract_logical_cc(a, b, result)
        self.psw.ia = wrap24(i.address + i.length)

    def op_N(self, i: Instruction) -> None:
        self.r[i.r1] &= self.fetch_u32(self._rx_addr(i), i.address)
        self.cc = 0 if self.r[i.r1] == 0 else 1
        self.psw.ia = wrap24(i.address + i.length)

    def op_O(self, i: Instruction) -> None:
        self.r[i.r1] |= self.fetch_u32(self._rx_addr(i), i.address)
        self.cc = 0 if self.r[i.r1] == 0 else 1
        self.psw.ia = wrap24(i.address + i.length)

    def op_X(self, i: Instruction) -> None:
        self.r[i.r1] ^= self.fetch_u32(self._rx_addr(i), i.address)
        self.cc = 0 if self.r[i.r1] == 0 else 1
        self.psw.ia = wrap24(i.address + i.length)

    def op_C(self, i: Instruction) -> None:
        a = s32(self.r[i.r1])
        b = s32(self.fetch_u32(self._rx_addr(i), i.address))
        self.cc = 0 if a == b else (1 if a < b else 2)
        self.psw.ia = wrap24(i.address + i.length)

    def op_CL(self, i: Instruction) -> None:
        a = self.r[i.r1] & MASK32
        b = self.fetch_u32(self._rx_addr(i), i.address)
        self.cc = 0 if a == b else (1 if a < b else 2)
        self.psw.ia = wrap24(i.address + i.length)

    def op_M(self, i: Instruction) -> None:
        if i.r1 % 2:
            raise SpecificationException("M odd first operand", i.address)
        product = s32(self.r[i.r1 + 1]) * s32(self.fetch_u32(self._rx_addr(i), i.address))
        self.r[i.r1] = (product >> 32) & MASK32
        self.r[i.r1 + 1] = product & MASK32
        self.psw.ia = wrap24(i.address + i.length)

    def op_D(self, i: Instruction) -> None:
        if i.r1 % 2:
            raise SpecificationException("D odd first operand", i.address)
        divisor = s32(self.fetch_u32(self._rx_addr(i), i.address))
        dividend = s64((self.r[i.r1] << 32) | self.r[i.r1 + 1])
        if divisor == 0:
            raise FixedPointDivide("division by zero", i.address)
        quotient, remainder = _trunc_divmod(dividend, divisor)
        if not (-2**31 <= quotient < 2**31):
            raise FixedPointDivide("quotient overflow", i.address)
        # Remainder to the even register, quotient to the odd (p. 128).
        self.r[i.r1] = remainder & MASK32
        self.r[i.r1 + 1] = quotient & MASK32
        self.psw.ia = wrap24(i.address + i.length)

    def op_AH(self, i: Instruction) -> None:
        v = s32(self.fetch_u16(self._rx_addr(i), i.address))
        v = v - 0x10000 if v & 0x8000 else v
        self.r[i.r1] = self._set_add_cc(s32(self.r[i.r1]) + v, i.address)
        self.psw.ia = wrap24(i.address + i.length)

    def op_SH(self, i: Instruction) -> None:
        v = self.fetch_u16(self._rx_addr(i), i.address)
        v = v - 0x10000 if v & 0x8000 else v
        self.r[i.r1] = self._set_add_cc(s32(self.r[i.r1]) - v, i.address)
        self.psw.ia = wrap24(i.address + i.length)

    def op_CH(self, i: Instruction) -> None:
        v = self.fetch_u16(self._rx_addr(i), i.address)
        v = v - 0x10000 if v & 0x8000 else v
        a = s32(self.r[i.r1])
        self.cc = 0 if a == v else (1 if a < v else 2)
        self.psw.ia = wrap24(i.address + i.length)

    def op_MH(self, i: Instruction) -> None:
        v = self.fetch_u16(self._rx_addr(i), i.address)
        v = v - 0x10000 if v & 0x8000 else v
        self.r[i.r1] = (s32(self.r[i.r1]) * v) & MASK32
        self.psw.ia = wrap24(i.address + i.length)

    def op_CVD(self, i: Instruction) -> None:
        value = s32(self.r[i.r1])
        addr = self._rx_addr(i)
        negative = value < 0
        digits = f"{abs(value):015d}"  # 15 digits, then a sign nibble = 8 bytes
        packed = bytearray(8)
        for idx in range(7):
            packed[idx] = (int(digits[2 * idx]) << 4) | int(digits[2 * idx + 1])
        packed[7] = (int(digits[14]) << 4) | (0x0D if negative else 0x0C)
        self.write(addr, bytes(packed))
        self.psw.ia = wrap24(i.address + i.length)

    def op_CVB(self, i: Instruction) -> None:
        addr = self._rx_addr(i)
        data = self.read(addr, 8)
        digits = []
        for byte in data:
            digits.append(byte >> 4)
            digits.append(byte & 0xF)
        sign_nibble = digits.pop()
        if sign_nibble not in (0xA, 0xC, 0xE, 0xF, 0xB, 0xD):
            raise ProgramCheck(0x07, "invalid packed sign", i.address)
        if any(d > 9 for d in digits):
            raise ProgramCheck(0x07, "invalid packed digit", i.address)
        value = int("".join(str(d) for d in digits))
        if sign_nibble in (0xB, 0xD):
            value = -value
        # "For any decimal number outside this range, the operation is
        # completed by placing the 32 low-order binary bits in the register; a
        # fixed-point divide exception exists" (GA22-7000-4, p. 128).
        self.r[i.r1] = value & MASK32
        if not (-2**31 <= value < 2**31):
            raise FixedPointDivide("CVB value out of range", i.address)
        self.psw.ia = wrap24(i.address + i.length)

    def op_BC(self, i: Instruction) -> None:
        mask = i.r1
        if mask & (1 << (3 - self.cc)):
            self.psw.ia = self._rx_addr(i)
        else:
            self.psw.ia = wrap24(i.address + i.length)

    def op_BAL(self, i: Instruction) -> None:
        target = self._rx_addr(i)
        if i.r1:
            self.r[i.r1] = self._link_word(i)
        self.psw.ia = target

    def op_BCT(self, i: Instruction) -> None:
        # The address is computed from the register *before* it is decremented,
        # which matters when R1 is also the base or index register.
        target = self._rx_addr(i)
        self.r[i.r1] = (self.r[i.r1] - 1) & MASK32
        if self.r[i.r1] != 0:
            self.psw.ia = target
        else:
            self.psw.ia = wrap24(i.address + i.length)

    def op_EX(self, i: Instruction) -> None:
        subject_addr = self._rx_addr(i)
        if subject_addr % 2:
            raise SpecificationException("EX effective address is odd", i.address)
        try:
            subject = decode(self.memory, subject_addr)
        except ValueError as exc:
            raise OperationException(str(exc), i.address) from None
        if subject.name == "EX":
            # "When the subject instruction is in turn an EXECUTE, an execute
            # exception is recognized, and the operation is suppressed."
            raise ProgramCheck(0x03, "EX of EX", i.address)
        # "Bits 8-15 of the instruction ... are ORed with bits 24-31 of the
        # register specified by R1, except when register 0 is specified, which
        # indicates that no modification takes place" (p. 129).
        patched = bytearray(subject.raw)
        if i.r1:
            patched[1] |= self.r[i.r1] & 0xFF
        saved = bytes(self.memory[subject_addr : subject_addr + len(patched)])
        self.memory[subject_addr : subject_addr + len(patched)] = patched
        try:
            patched_ins = decode(self.memory, subject_addr)
            patched_ins = _with_address(patched_ins, i.address)
            handler = getattr(self, f"op_{patched_ins.name}", None)
            if handler is None:
                raise OperationException(f"{patched_ins.name} not implemented", i.address)
            handler(patched_ins)
        finally:
            self.memory[subject_addr : subject_addr + len(patched)] = saved

    def op_LRA(self, i: Instruction) -> None:
        # LOAD REAL ADDRESS is a privileged instruction.
        raise PrivilegedOperation("LRA", i.address)

    # ---------------- RS ----------------

    def _rs_addr(self, i: Instruction) -> int:
        return self.addr(i.b2, i.d2, 0, i.address)

    def _shift_amount(self, i: Instruction) -> int:
        # "The second-operand address is not used to address data; its
        # low-order six bits indicate the number of bit positions to be
        # shifted." (GA22-7000-4, SLA and friends)
        return self.addr(i.b2, i.d2) & 0x3F

    def op_SLL(self, i: Instruction) -> None:
        n = self._shift_amount(i)
        self.r[i.r1] = (self.r[i.r1] << n) & MASK32
        self.psw.ia = wrap24(i.address + i.length)

    def op_SRL(self, i: Instruction) -> None:
        n = self._shift_amount(i)
        self.r[i.r1] = (self.r[i.r1] & MASK32) >> n
        self.psw.ia = wrap24(i.address + i.length)

    def _shift_arith_cc(self, value: int, before: int) -> None:
        """CC for SLA/SRA: 0 zero, 1 negative, 2 positive, 3 overflow.

        Overflow for a left arithmetic shift means a bit shifted out of bit 0
        differed from the original sign bit (GA22-7000-4).
        """
        v = s32(value)
        self.cc = 0 if v == 0 else (1 if v < 0 else 2)

    def op_SLA(self, i: Instruction) -> None:
        """Shift Left Single arithmetic.

        "The sign of the first operand remains unchanged. All 31 integer bits
        of the operand participate in the left shift... If a bit unlike the
        sign bit is shifted out of position 1, an overflow occurs."
        (GA22-7000-4, p. 139)
        """
        n = self._shift_amount(i)
        before = self.r[i.r1] & MASK32
        sign = before & 0x80000000
        magnitude = before & 0x7FFFFFFF
        overflow = False
        if n:
            lost = magnitude >> (31 - n) if n < 31 else magnitude
            expected = 0 if not sign else (1 << n) - 1 if n < 31 else 0x7FFFFFFF
            overflow = lost != expected
        result = sign | ((magnitude << n) & 0x7FFFFFFF)
        self.r[i.r1] = result
        self.cc = 3 if overflow else (0 if result == 0 else (1 if result & 0x80000000 else 2))
        self.psw.ia = wrap24(i.address + i.length)

    def op_SRA(self, i: Instruction) -> None:
        n = self._shift_amount(i)
        before = s32(self.r[i.r1])
        self.r[i.r1] = (before >> n) & MASK32
        self._shift_arith_cc(self.r[i.r1], before)
        self.psw.ia = wrap24(i.address + i.length)

    def _double(self, i: Instruction) -> int:
        if i.r1 % 2:
            raise SpecificationException(f"{i.name} odd first operand", i.address)
        return (self.r[i.r1] << 32) | self.r[i.r1 + 1]

    def op_SLDL(self, i: Instruction) -> None:
        n = self._shift_amount(i)
        v = (self._double(i) << n) & 0xFFFF_FFFF_FFFF_FFFF
        self.r[i.r1] = (v >> 32) & MASK32
        self.r[i.r1 + 1] = v & MASK32
        self.psw.ia = wrap24(i.address + i.length)

    def op_SRDL(self, i: Instruction) -> None:
        n = self._shift_amount(i)
        v = self._double(i) >> n
        self.r[i.r1] = (v >> 32) & MASK32
        self.r[i.r1 + 1] = v & MASK32
        self.psw.ia = wrap24(i.address + i.length)

    def op_SLDA(self, i: Instruction) -> None:
        """Shift Left Double arithmetic: the sign of the even register is
        preserved; the 63 integer bits shift left."""
        n = self._shift_amount(i)
        before = self._double(i)
        sign = before & 0x8000_0000_0000_0000
        magnitude = before & 0x7FFF_FFFF_FFFF_FFFF
        overflow = False
        if n:
            lost = magnitude >> (63 - n) if n < 63 else magnitude
            expected = 0 if not sign else (1 << n) - 1 if n < 63 else 0x7FFF_FFFF_FFFF_FFFF
            overflow = lost != expected
        result = sign | ((magnitude << n) & 0x7FFF_FFFF_FFFF_FFFF)
        self.r[i.r1] = (result >> 32) & MASK32
        self.r[i.r1 + 1] = result & MASK32
        self.cc = 3 if overflow else (0 if result == 0 else (1 if result & 0x8000_0000_0000_0000 else 2))
        self.psw.ia = wrap24(i.address + i.length)

    def op_SRDA(self, i: Instruction) -> None:
        n = self._shift_amount(i)
        v = s64(self._double(i)) >> n
        result = v & 0xFFFF_FFFF_FFFF_FFFF
        self.r[i.r1] = (result >> 32) & MASK32
        self.r[i.r1 + 1] = result & MASK32
        self.cc = 0 if v == 0 else (1 if v < 0 else 2)
        self.psw.ia = wrap24(i.address + i.length)

    def op_STM(self, i: Instruction) -> None:
        addr = self._rs_addr(i)
        r = i.r1
        while True:
            self.store_u32(addr, self.r[r], i.address)
            addr = wrap24(addr + 4)
            if r == i.r3:
                break
            r = (r + 1) % 16
        self.psw.ia = wrap24(i.address + i.length)

    def op_LM(self, i: Instruction) -> None:
        addr = self._rs_addr(i)
        r = i.r1
        while True:
            self.r[r] = self.fetch_u32(addr, i.address)
            addr = wrap24(addr + 4)
            if r == i.r3:
                break
            r = (r + 1) % 16
        self.psw.ia = wrap24(i.address + i.length)

    @staticmethod
    def comparand_register(r3: int) -> int:
        """BXH/BXLE comparand: "the comparand register address is odd and is
        either one larger than R3 or equal to R3" (GA22-7000-4, p. 122)."""
        return r3 if r3 % 2 else (r3 + 1) % 16

    def _bx(self, i: Instruction, branch_if) -> None:
        cr = self.comparand_register(i.r3)
        # If the index and comparand are the same register, the *original*
        # contents are used as the comparand.
        comparand = s32(self.r[i.r1]) if cr == i.r1 else s32(self.r[cr])
        # The branch address is computed before the addition and comparison,
        # and the increment is applied whether or not the branch is taken.
        target = self._rs_addr(i)
        self.r[i.r1] = (self.r[i.r1] + self.r[i.r3]) & MASK32
        if branch_if(s32(self.r[i.r1]), comparand):
            self.psw.ia = target
        else:
            self.psw.ia = wrap24(i.address + i.length)

    def op_BXH(self, i: Instruction) -> None:
        self._bx(i, lambda new, cmp: new > cmp)

    def op_BXLE(self, i: Instruction) -> None:
        self._bx(i, lambda new, cmp: new <= cmp)

    def op_CS(self, i: Instruction) -> None:
        addr = self._rs_addr(i)
        if addr % 4:
            raise SpecificationException("CS operand not word-aligned", i.address)
        old = self.fetch_u32(addr, i.address)
        if old == self.r[i.r1]:
            self.store_u32(addr, self.r[i.r3], i.address)
            self.cc = 0
        else:
            self.r[i.r1] = old
            self.cc = 1
        self.psw.ia = wrap24(i.address + i.length)

    def op_CDS(self, i: Instruction) -> None:
        if i.r1 % 2:
            raise SpecificationException("CDS odd first operand", i.address)
        addr = self._rs_addr(i)
        if addr % 8:
            raise SpecificationException("CDS operand not doubleword-aligned", i.address)
        old1 = self.fetch_u32(addr, i.address)
        old2 = self.fetch_u32(addr + 4, i.address)
        if old1 == self.r[i.r1] and old2 == self.r[i.r1 + 1]:
            self.store_u32(addr, self.r[i.r3], i.address)
            self.store_u32(addr + 4, self.r[i.r3 + 1], i.address)
            self.cc = 0
        else:
            self.r[i.r1] = old1
            self.r[i.r1 + 1] = old2
            self.cc = 1
        self.psw.ia = wrap24(i.address + i.length)

    def _mask_bytes(self, mask: int) -> list[int]:
        """Byte offsets selected by a 4-bit mask, bit 0 = leftmost byte."""
        return [k for k in range(4) if mask & (1 << (3 - k))]

    def op_CLM(self, i: Instruction) -> None:
        # Contiguous second operand, as for ICM/STCM; a zero mask is CC 0.
        addr = self._rs_addr(i)
        offsets = self._mask_bytes(i.r3)
        if not offsets:
            self.cc = 0
            self.psw.ia = wrap24(i.address + i.length)
            return
        left = [(self.r[i.r1] >> (24 - 8 * k)) & 0xFF for k in offsets]
        right = [self.fetch_byte(addr + position, i.address) for position in range(len(offsets))]
        if left == right:
            self.cc = 0
        elif left < right:
            self.cc = 1
        else:
            self.cc = 2
        self.psw.ia = wrap24(i.address + i.length)

    def op_STCM(self, i: Instruction) -> None:
        # "The bytes corresponding to ones in the mask are placed in the same
        # order in successive and contiguous storage locations" (p. 141).
        addr = self._rs_addr(i)
        for position, k in enumerate(self._mask_bytes(i.r3)):
            self.store_byte(addr + position, (self.r[i.r1] >> (24 - 8 * k)) & 0xFF, i.address)
        self.psw.ia = wrap24(i.address + i.length)

    def op_ICM(self, i: Instruction) -> None:
        """Insert Characters Under Mask.

        Condition code, per GA22-7000-4 p. 130: 0 when the mask is zero or all
        inserted bytes are zero; otherwise 1 or 2 according to **the leftmost
        bit of the storage operand** -- bit 0 of the first byte fetched, not
        the first byte that happens to have a high bit set.
        """
        addr = self._rs_addr(i)
        offsets = self._mask_bytes(i.r3)
        if not offsets:
            # A zero mask inserts nothing: CC 0, no access, no exception, and
            # the register is unchanged.
            self.cc = 0
            self.psw.ia = wrap24(i.address + i.length)
            return
        # "Bytes are fetched from contiguous storage locations beginning at the
        # second-operand address. The bytes in the general register
        # corresponding to zeros in the mask remain unchanged" (p. 130).
        value = self.r[i.r1]
        first_byte = None
        for position, k in enumerate(offsets):
            byte = self.fetch_byte(addr + position, i.address)
            if first_byte is None:
                first_byte = byte
            shift = 24 - 8 * k
            value = (value & ~(0xFF << shift)) | (byte << shift)
        self.r[i.r1] = value
        if value == 0:
            self.cc = 0
        else:
            self.cc = 1 if (first_byte & 0x80) else 2
        self.psw.ia = wrap24(i.address + i.length)

    # ---------------- SI ----------------

    def _si_addr(self, i: Instruction) -> int:
        return self.addr(i.b2, i.d2, 0, i.address)

    def op_TM(self, i: Instruction) -> None:
        byte = self.fetch_byte(self._si_addr(i), i.address)
        result = byte & i.i2
        if result == 0:
            self.cc = 0
        elif result == i.i2:
            self.cc = 3
        else:
            self.cc = 1
        self.psw.ia = wrap24(i.address + i.length)

    def op_MVI(self, i: Instruction) -> None:
        self.store_byte(self._si_addr(i), i.i2, i.address)
        self.psw.ia = wrap24(i.address + i.length)

    def op_CLI(self, i: Instruction) -> None:
        byte = self.fetch_byte(self._si_addr(i), i.address)
        self.cc = 0 if byte == i.i2 else (1 if byte < i.i2 else 2)
        self.psw.ia = wrap24(i.address + i.length)

    def op_NI(self, i: Instruction) -> None:
        addr = self._si_addr(i)
        value = self.fetch_byte(addr, i.address) & i.i2
        self.store_byte(addr, value, i.address)
        self.cc = 0 if value == 0 else 1
        self.psw.ia = wrap24(i.address + i.length)

    def op_OI(self, i: Instruction) -> None:
        addr = self._si_addr(i)
        value = self.fetch_byte(addr, i.address) | i.i2
        self.store_byte(addr, value, i.address)
        self.cc = 0 if value == 0 else 1
        self.psw.ia = wrap24(i.address + i.length)

    def op_XI(self, i: Instruction) -> None:
        addr = self._si_addr(i)
        value = self.fetch_byte(addr, i.address) ^ i.i2
        self.store_byte(addr, value, i.address)
        self.cc = 0 if value == 0 else 1
        self.psw.ia = wrap24(i.address + i.length)

    def op_STNSM(self, i: Instruction) -> None:
        raise PrivilegedOperation("STNSM", i.address)

    def op_STOSM(self, i: Instruction) -> None:
        raise PrivilegedOperation("STOSM", i.address)

    # ---------------- S ----------------

    def op_SSM(self, i: Instruction) -> None:
        raise PrivilegedOperation("SSM", i.address)

    def op_LPSW(self, i: Instruction) -> None:
        # LPSW is a privileged instruction; a problem-state program that
        # executes it takes a privileged-operation program check.
        raise PrivilegedOperation("LPSW", i.address)

    def op_TS(self, i: Instruction) -> None:
        # "The leftmost bit (bit position 0) of the byte ... is used to set the
        # condition code, and then the entire addressed byte is set to all
        # ones" (GA22-7000-4, p. 144). CC 0 = leftmost bit zero, 1 = one.
        addr = self._si_addr(i)
        byte = self.fetch_byte(addr, i.address)
        self.store_byte(addr, 0xFF, i.address)
        self.cc = 1 if byte & 0x80 else 0
        self.psw.ia = wrap24(i.address + i.length)

    def op_DIAG(self, i: Instruction) -> None:
        """DIAG is architecturally a no-op here.

        The emulator's test programs use `DIAG 0,0,0` as a halt convention,
        which is *our* convention, not IBM's. Real DIAG code does whatever the
        microcode says; this implementation does not model it.
        """
        if i.d2 == 0 and i.b2 == 0:
            self.halted = True
            raise Halt()
        self.psw.ia = wrap24(i.address + i.length)

    def op_WRD(self, i: Instruction) -> None:
        raise PrivilegedOperation("WRD", i.address)

    def op_RDD(self, i: Instruction) -> None:
        raise PrivilegedOperation("RDD", i.address)

    # ---------------- SS ----------------

    def _ss_addrs(self, i: Instruction) -> tuple[int, int]:
        """SS operand addresses, checked against the end of store.

        Every SS instruction accesses at least one byte at each operand, so the
        check can be done here once rather than in each handler.
        """
        a1 = self.addr(i.b1, i.d1, 0, i.address)
        a2 = self.addr(i.b2, i.d2, 0, i.address)
        first_len = max(i.l1 + 1, i.l2 + 1)
        self._check(a1, 1, i.address)
        self._check(a2, 1, i.address)
        # A field that runs past the end of store is an addressing exception.
        self._check(a2, max(first_len, i.l1 + 1, i.l2 + 1), i.address)
        return a1, a2

    # ---------------- packed decimal ----------------

    # Sign nibbles: 10, 12, 14 and 15 are plus; 11 and 13 are minus. Results are
    # always written with the preferred codes 12 (plus) and 13 (minus)
    # (GA22-7000-4, p. 148).
    PLUS_SIGNS = frozenset({0xA, 0xC, 0xE, 0xF})
    MINUS_SIGNS = frozenset({0xB, 0xD})
    SIGN_PLUS = 0xC
    SIGN_MINUS = 0xD

    def _decimal_in(self, addr: int, length: int, what: str = "operand") -> int:
        """Read a packed-decimal field and return its signed integer value."""
        data = self.read(addr, length)
        digits = []
        for byte in data:
            digits.append(byte >> 4)
            digits.append(byte & 0x0F)
        sign = digits.pop()
        if sign not in self.PLUS_SIGNS and sign not in self.MINUS_SIGNS:
            raise ProgramCheck(0x07, f"invalid packed sign in {what}", None)
        if any(d > 9 for d in digits):
            raise ProgramCheck(0x07, f"invalid packed digit in {what}", None)
        value = int("".join(str(d) for d in digits)) if digits else 0
        return -value if sign in self.MINUS_SIGNS else value

    def _decimal_out(self, addr: int, length: int, value: int) -> None:
        """Write *value* into a packed field of *length* bytes, right-aligned."""
        capacity = 2 * length - 1
        magnitude = abs(value)
        digits = f"{magnitude:0{capacity}d}"[-capacity:] if capacity else ""
        negative = value < 0
        out = bytearray(length)
        for index in range(length):
            hi = int(digits[2 * index]) if 2 * index < len(digits) else 0
            lo_index = 2 * index + 1
            lo = int(digits[lo_index]) if lo_index < len(digits) else 0
            out[index] = (hi << 4) | lo
        out[length - 1] = (out[length - 1] & 0xF0) | (
            self.SIGN_MINUS if negative else self.SIGN_PLUS
        )
        self.write(addr, bytes(out))

    def _decimal_overflow(self, what: str, address: int) -> None:
        """Decimal overflow: CC 3 is already set; interrupt if the mask allows."""
        if self.psw.pmask & 0x8:            # program mask bit 0
            raise ProgramCheck(0x0A, what, address)

    def _decimal_op(self, i: Instruction, op: str) -> None:
        """Shared body of AP, SP, CP, ZAP, MP and DP.

        Results never exceed the first operand's field: surplus high-order
        digits are lost, which is a decimal overflow (GA22-7000-4, p. 147).
        """
        a1, a2 = self._ss_addrs(i)
        l1 = i.l1 + 1
        l2 = i.l2 + 1
        capacity = 2 * l1 - 1

        if op == "ZAP":
            # "Only the second operand is checked for valid sign and digit
            # codes" (p. 155).
            value = self._decimal_in(a2, l2, "second operand")
            overflow = abs(value) >= 10 ** capacity
            self._decimal_out(a1, l1, value)
        elif op == "CP":
            left = self._decimal_in(a1, l1, "first operand")
            right = self._decimal_in(a2, l2, "second operand")
            self.cc = 0 if left == right else (1 if left < right else 2)
            self.psw.ia = wrap24(i.address + i.length)
            return
        elif op in ("AP", "SP"):
            left = self._decimal_in(a1, l1, "first operand")
            right = self._decimal_in(a2, l2, "second operand")
            # SUBTRACT DECIMAL negates the second operand's sign (p. 154).
            value = left + right if op == "AP" else left - right
            overflow = abs(value) >= 10 ** capacity
            self._decimal_out(a1, l1, value)
        elif op == "MP":
            if l2 > 8 or l2 >= l1:
                raise SpecificationException("MP multiplier too large", i.address)
            left = self._decimal_in(a1, l1, "multiplicand")
            right = self._decimal_in(a2, l2, "multiplier")
            value = left * right
            overflow = abs(value) >= 10 ** capacity
            self._decimal_out(a1, l1, value)
            # "Condition Code: The code remains unchanged" (p. 153).
            self.psw.ia = wrap24(i.address + i.length)
            if overflow:
                self._decimal_overflow("MP overflow", i.address)
            return
        elif op == "DP":
            if l2 > 8 or l2 >= l1:
                raise SpecificationException("DP divisor too large", i.address)
            dividend = self._decimal_in(a1, l1, "dividend")
            divisor = self._decimal_in(a2, l2, "divisor")
            if divisor == 0:
                raise ProgramCheck(0x0B, "decimal divide by zero", i.address)
            quotient, remainder = _trunc_divmod(dividend, divisor)
            quotient_bytes = l1 - l2
            if abs(quotient) >= 10 ** (2 * quotient_bytes - 1):
                # Decimal divide: program mask bit 2.
                if self.psw.pmask & 0x2:
                    raise ProgramCheck(0x0B, "decimal divide quotient overflow", i.address)
            self._decimal_out(a1, quotient_bytes, quotient)
            self._decimal_out(a1 + quotient_bytes, l2, remainder)
            self.psw.ia = wrap24(i.address + i.length)
            return
        else:  # pragma: no cover
            raise OperationException(f"decimal op {op}", i.address)

        value = self._decimal_in(a1, l1, "first operand") if op != "ZAP" else None
        # Condition code from the stored result.
        stored = self._decimal_in(a1, l1, "result")
        if overflow:
            self.cc = 3
            self.psw.ia = wrap24(i.address + i.length)
            self._decimal_overflow(f"{op} overflow", i.address)
            return
        self.cc = 0 if stored == 0 else (1 if stored < 0 else 2)
        self.psw.ia = wrap24(i.address + i.length)

    def op_AP(self, i: Instruction) -> None:
        self._decimal_op(i, "AP")

    def op_SP(self, i: Instruction) -> None:
        self._decimal_op(i, "SP")

    def op_CP(self, i: Instruction) -> None:
        self._decimal_op(i, "CP")

    def op_ZAP(self, i: Instruction) -> None:
        self._decimal_op(i, "ZAP")

    def op_MP(self, i: Instruction) -> None:
        self._decimal_op(i, "MP")

    def op_DP(self, i: Instruction) -> None:
        self._decimal_op(i, "DP")

    # ---------------- remaining decimal instructions ----------------

    def _nibbles(self, addr: int, length: int) -> list[int]:
        out: list[int] = []
        for byte in self.read(addr, length):
            out.append(byte >> 4)
            out.append(byte & 0x0F)
        return out

    def _write_nibbles(self, addr: int, length: int, nibbles: list[int]) -> None:
        capacity = 2 * length
        if len(nibbles) > capacity:
            nibbles = nibbles[-capacity:]
        else:
            nibbles = [0] * (capacity - len(nibbles)) + nibbles
        out = bytearray(length)
        for index in range(length):
            out[index] = (nibbles[2 * index] << 4) | nibbles[2 * index + 1]
        self.write(addr, bytes(out))

    def op_MVO(self, i: Instruction) -> None:
        """MOVE WITH OFFSET.

        "The low-order four bits of the first operand are attached as low-order
        bits to the second operand, the second operand bits are offset by four
        bit positions, and the result is placed in the first-operand location"
        (GA22-7000-4, p. 135). No code checking is done.
        """
        a1, a2 = self._ss_addrs(i)
        l1, l2 = i.l1 + 1, i.l2 + 1
        low = self.memory[a1 + l1 - 1] & 0x0F
        combined = self._nibbles(a2, l2) + [low]
        self._write_nibbles(a1, l1, combined)
        self.psw.ia = wrap24(i.address + i.length)

    def op_SRP(self, i: Instruction) -> None:
        """SHIFT AND ROUND DECIMAL.

        The shift value is the low six bits of the second-operand address, as a
        signed number: positive shifts left, negative right (p. 153-154). For a
        right shift the rounding factor I3 -- the second nibble of the length
        byte -- is added to the digits shifted out, and the carry propagated.
        """
        a1, _a2 = self._ss_addrs(i)
        length = i.l1 + 1
        shift = self.addr(i.b2, i.d2) & 0x3F
        if shift & 0x20:
            shift -= 0x40
        rounding = i.l2              # I3 lives in the low half of the length byte

        digits = self._nibbles(a1, length)
        sign = digits.pop()
        if sign not in self.PLUS_SIGNS and sign not in self.MINUS_SIGNS:
            raise ProgramCheck(0x07, "invalid packed sign in SRP", i.address)
        if any(d > 9 for d in digits):
            raise ProgramCheck(0x07, "invalid packed digit in SRP", i.address)
        if rounding > 9:
            raise ProgramCheck(0x07, "invalid SRP rounding factor", i.address)

        capacity = len(digits)
        value = int("".join(str(d) for d in digits)) if digits else 0
        negative = sign in self.MINUS_SIGNS
        overflow = False

        if shift >= 0:
            # Left shift: digits move into the high-order positions.
            limit = 10 ** capacity
            scaled = value * (10 ** shift)
            if scaled >= limit:
                overflow = True
                scaled %= limit
            value = scaled
        else:
            right = -shift
            # Round by adding the rounding factor to the leftmost digit that
            # will be shifted out, propagating the carry.
            if right <= capacity:
                cut = 10 ** right
                low = value % cut
                high = value // cut
                if rounding and low >= cut // 10 * (10 - rounding):
                    pass
                # Add the rounding factor to the digit position being dropped.
                add = rounding * (10 ** (right - 1))
                total = low + add
                high += total // cut
                value = high
                if high >= 10 ** capacity:
                    overflow = True
                    value = high % (10 ** capacity)
            else:
                value = 0

        self._decimal_out(a1, length, -value if negative else value)
        if value == 0:
            self.cc = 0
        else:
            self.cc = 1 if negative else 2
        self.psw.ia = wrap24(i.address + i.length)
        # "Decimal overflow occurs ... during left shift" (p. 154).
        if overflow and shift > 0:
            self.cc = 3
            self._decimal_overflow("SRP overflow", i.address)

    def _edit(self, i: Instruction, mark: bool) -> None:
        """EDIT / EDIT AND MARK.

        The length field applies only to the pattern; source bytes are fetched
        on demand, two digits at a time, except that a byte whose low-order
        nibble is a sign code yields only one digit (GA22-7000-4, p. 151).
        """
        pattern, source = self._ss_addrs(i)
        length = i.l1 + 1
        pattern_bytes = list(self.read(pattern, length))
        if not pattern_bytes:
            return

        DIGIT_SELECTOR, SIGNIFICANCE_STARTER, FIELD_SEPARATOR = 0x20, 0x21, 0x22
        fill = pattern_bytes[0]

        src_pos = 0
        pending: tuple[int, bool] | None = None

        def next_digit() -> tuple[int, bool]:
            nonlocal src_pos, pending
            if pending is not None:
                out, pending = pending, None
                return out
            byte = self.fetch_byte(source + src_pos, i.address)
            src_pos += 1
            hi, lo = byte >> 4, byte & 0x0F
            if hi > 9:
                raise ProgramCheck(0x07, "invalid packed digit in EDIT source", i.address)
            if lo in self.PLUS_SIGNS:
                return hi, True
            if lo in self.MINUS_SIGNS:
                return hi, False
            if lo > 9:
                raise ProgramCheck(0x07, "invalid packed digit in EDIT source", i.address)
            pending = (lo, False)
            return hi, False

        indicator = False
        field_nonzero = False
        result = bytearray(length)

        for index, char in enumerate(pattern_bytes):
            if char == DIGIT_SELECTOR or char == SIGNIFICANCE_STARTER:
                was_off = not indicator
                digit, plus = next_digit()
                if digit:
                    field_nonzero = True
                if (char == DIGIT_SELECTOR and was_off and digit == 0) or (
                    char == SIGNIFICANCE_STARTER and digit == 0
                ):
                    result[index] = fill
                else:
                    result[index] = 0xF0 | digit
                    indicator = not plus
                if mark and was_off and digit:
                    self.r[1] = (self.r[1] & 0xFF000000) | (pattern + index)
            elif char == FIELD_SEPARATOR:
                result[index] = fill
                indicator = False
                field_nonzero = False
            else:
                result[index] = char if indicator else fill

        self.write(pattern, bytes(result))
        self.cc = 0 if not field_nonzero else (1 if indicator else 2)
        self.psw.ia = wrap24(i.address + i.length)

    def op_ED(self, i: Instruction) -> None:
        self._edit(i, mark=False)

    def op_EDMK(self, i: Instruction) -> None:
        self._edit(i, mark=True)

    def op_MVC(self, i: Instruction) -> None:
        dest, src = self._ss_addrs(i)
        n = i.l + 1
        # "When the operands overlap, the result is obtained as if each result
        # byte were stored immediately after the necessary operand byte is
        # fetched" (GA22-7000-4, MOVE). So: byte at a time, left to right.
        for k in range(n):
            self.memory[(dest + k) & MASK24] = self.memory[(src + k) & MASK24]
        self.psw.ia = wrap24(i.address + i.length)

    def op_MVN(self, i: Instruction) -> None:
        # MOVE NUMERICS: the low-order four bits (the numeric bits) of each
        # second-operand byte replace the numeric bits of the first operand.
        dest, src = self._ss_addrs(i)
        for k in range(i.l1 + 1):
            self.memory[dest + k] = (self.memory[dest + k] & 0xF0) | (self.memory[src + k] & 0x0F)
        self.psw.ia = wrap24(i.address + i.length)

    def op_MVZ(self, i: Instruction) -> None:
        # MOVE ZONES: "The high-order four bits of each byte in the
        # second-operand field (the zones) are placed in the high-order four
        # bit positions of the corresponding bytes in the first-operand field"
        # (GA22-7000-4, p. 136).
        dest, src = self._ss_addrs(i)
        for k in range(i.l1 + 1):
            self.memory[dest + k] = (self.memory[dest + k] & 0x0F) | (self.memory[src + k] & 0xF0)
        self.psw.ia = wrap24(i.address + i.length)

    def _ss_logic(self, i: Instruction, op) -> None:
        dest, src = self._ss_addrs(i)
        all_zero = True
        for k in range(i.l + 1):
            value = op(self.memory[dest + k], self.memory[src + k]) & 0xFF
            self.memory[dest + k] = value
            if value:
                all_zero = False
        self.cc = 0 if all_zero else 1
        self.psw.ia = wrap24(i.address + i.length)

    def op_NC(self, i: Instruction) -> None:
        self._ss_logic(i, lambda a, b: a & b)

    def op_OC(self, i: Instruction) -> None:
        self._ss_logic(i, lambda a, b: a | b)

    def op_XC(self, i: Instruction) -> None:
        self._ss_logic(i, lambda a, b: a ^ b)

    def op_CLC(self, i: Instruction) -> None:
        a, b = self._ss_addrs(i)
        n = i.l + 1
        left = bytes(self.memory[a : a + n])
        right = bytes(self.memory[b : b + n])
        if left == right:
            self.cc = 0
        elif left < right:
            self.cc = 1
        else:
            self.cc = 2
        self.psw.ia = wrap24(i.address + i.length)

    def op_TR(self, i: Instruction) -> None:
        dest, table = self._ss_addrs(i)
        for k in range(i.l + 1):
            self.memory[dest + k] = self.memory[table + self.memory[dest + k]]
        self.psw.ia = wrap24(i.address + i.length)

    def op_TRT(self, i: Instruction) -> None:
        """TRANSLATE AND TEST.

        "the related argument address is inserted in the low-order 24 bits of
        general register 1... The function byte is inserted in the low-order
        eight bits of general register 2" (GA22-7000-4, p. 146). CC 1 when
        argument bytes remain, CC 2 when the last byte is the only hit.
        """
        dest, table = self._ss_addrs(i)
        n = i.l1 + 1
        for k in range(n):
            function = self.memory[table + self.memory[dest + k]]
            if function:
                self.r[1] = (self.r[1] & 0xFF000000) | ((dest + k) & MASK24)
                self.r[2] = (self.r[2] & 0xFFFFFF00) | function
                self.cc = 2 if k == n - 1 else 1
                self.psw.ia = wrap24(i.address + i.length)
                return
        # "the contents of general registers 1 and 2 remain unchanged"
        self.cc = 0
        self.psw.ia = wrap24(i.address + i.length)

    def op_PACK(self, i: Instruction) -> None:
        """Zoned -> packed decimal.

        Per GA22-7000-4: zones are ignored except the zone over the low-order
        digit, which is the sign; the sign occupies the right four bits of the
        low-order result byte and the digits sit adjacent to it. Processing is
        as if right to left, with the result zero-extended on the left and any
        surplus high-order digits discarded.
        """
        dest, src = self._ss_addrs(i)
        n_src = i.l2 + 1
        n_dst = i.l1 + 1

        # Digested right-to-left: digit from each source byte's low nibble,
        # sign from the high nibble of the last (low-order) source byte.
        digits = [self.memory[src + k] & 0x0F for k in range(n_src - 1, -1, -1)]
        sign = (self.memory[src + n_src - 1] >> 4) & 0x0F

        capacity = 2 * n_dst - 1
        digits = digits[:capacity]          # discard high-order overflow
        digits += [0] * (capacity - len(digits))

        nibbles = list(reversed(digits)) + [sign]
        out = bytearray(n_dst)
        for idx in range(n_dst):
            hi = nibbles[2 * idx]
            lo = nibbles[2 * idx + 1]
            out[idx] = (hi << 4) | lo
        self.write(dest, bytes(out))
        self.psw.ia = wrap24(i.address + i.length)

    def op_UNPK(self, i: Instruction) -> None:
        """Packed -> zoned decimal, the inverse of PACK.

        The result field is L+1 bytes = 2(L+1) nibbles: 2L+1 digits plus a sign
        in the high nibble of the low-order byte. Digits are right-aligned and
        the field is zero-filled on the left.
        """
        dest, src = self._ss_addrs(i)
        n_src = i.l2 + 1
        n_dst = i.l1 + 1

        nibbles: list[int] = []
        for k in range(n_src):
            byte = self.memory[src + k]
            nibbles.append(byte >> 4)
            nibbles.append(byte & 0x0F)
        sign = nibbles.pop()

        capacity = 2 * n_dst - 1
        digits = nibbles[-capacity:] if capacity else []
        digits = [0] * (capacity - len(digits)) + digits

        out = bytearray(n_dst)
        for idx in range(n_dst):
            out[idx] = 0xF0 | digits[idx]
        if n_dst:
            out[-1] = (sign << 4) | digits[-1]
        self.write(dest, bytes(out))
        self.psw.ia = wrap24(i.address + i.length)

    def _privileged_ss(self, i: Instruction) -> None:
        # The decimal-feature instructions are unprivileged; this emulator does
        # not implement the decimal feature, so executing one is an operation
        # exception, not a privileged-operation exception.
        raise OperationException(f"{i.name} (decimal feature not implemented)", i.address)

    # ---------------- helpers used by tests and the CLI ----------------

    def load(self, address: int, data: bytes) -> None:
        self.write(address, data)

    def snapshot(self) -> dict:
        return {
            "r": list(self.r),
            "cc": self.cc,
            "ia": self.psw.ia,
            "steps": self.steps,
        }


def _trunc_divmod(a: int, b: int) -> tuple[int, int]:
    """Truncating division (C semantics), which is what S/370 specifies."""
    q = abs(a) // abs(b)
    if (a < 0) != (b < 0):
        q = -q
    return q, a - q * b


def _with_address(ins: Instruction, address: int) -> Instruction:
    """Re-home a decoded instruction at the EX address so that its length
    advance is taken from the executing instruction, not the subject."""
    return Instruction(
        address=address,
        opcode=ins.opcode,
        name=ins.name,
        fmt=ins.fmt,
        length=4,  # EX always advances by the RX instruction's length
        b1=ins.b1, d1=ins.d1, b2=ins.b2, d2=ins.d2,
        r1=ins.r1, r2=ins.r2, r3=ins.r3, i2=ins.i2,
        l1=ins.l1, l2=ins.l2, raw=ins.raw,
    )
