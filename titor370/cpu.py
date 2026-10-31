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
ED EDMK SRP MVO ZAP CP AP SP MP DP
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
        self.r[i.r1] = u32(-s32(self.r[i.r2]))
        self.cc = cc_for(self.r[i.r1])
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
        self.cc = 3  # overflow takes precedence over sign (GA22-7000-4)
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

    def op_SLR(self, i: Instruction) -> None:
        a, b = self.r[i.r1] & MASK32, self.r[i.r2] & MASK32
        s = a - b
        self.r[i.r1] = s & MASK32
        # SUBTRACT LOGICAL: carry means a borrow occurred (second operand larger)
        self._logical_add_cc(s, b > a)
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
        product = s32(self.r[i.r1]) * s32(self.r[i.r2])
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
        self.r[i.r1] = quotient & MASK32
        self.r[i.r1 + 1] = remainder & MASK32
        self.psw.ia = wrap24(i.address + i.length)

    def op_BALR(self, i: Instruction) -> None:
        target = self.r[i.r2] & MASK24
        if i.r1:
            self.r[i.r1] = (i.address + i.length) & MASK32
        if i.r2:
            self.psw.ia = target
        else:
            self.psw.ia = wrap24(i.address + i.length)

    def op_BCTR(self, i: Instruction) -> None:
        self.r[i.r1] = (self.r[i.r1] - 1) & MASK32
        if self.r[i.r1] != 0 and i.r2:
            self.psw.ia = self.r[i.r2] & MASK24
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
        """MOVE LONG: R1 = destination, R2 = source; R0 low byte is the pad.

        Condition code: 0 lengths equal, 1 first-operand length low, 2 high,
        3 overlap with identical address and length (no operation).
        """
        d = self.r[i.r1]
        src = self.r[i.r2]
        daddr, dlen = d & MASK24, (d >> 24) & 0xFF
        saddr, slen = src & MASK24, (src >> 24) & 0xFF
        pad = self.r[0] & 0xFF

        if daddr == saddr and dlen == slen:
            self.cc = 3
            self.psw.ia = wrap24(i.address + i.length)
            return

        n = min(dlen, slen)
        if n:
            chunk = bytes(self.memory[saddr : saddr + n])
            self.memory[daddr : daddr + n] = chunk
        if slen < dlen:
            fill = dlen - slen
            self.memory[daddr + slen : daddr + slen + fill] = bytes([pad]) * fill

        self.cc = 0 if dlen == slen else (1 if dlen < slen else 2)
        self.r[i.r1] = (((dlen - n) & 0xFF) << 24) | ((daddr + n) & MASK24)
        self.r[i.r2] = (((slen - n) & 0xFF) << 24) | ((saddr + n) & MASK24)
        self.psw.ia = wrap24(i.address + i.length)

    def op_CLCL(self, i: Instruction) -> None:
        a = self.r[i.r1]
        b = self.r[i.r2]
        aaddr, alen = a & MASK24, (a >> 24) & 0xFF
        baddr, blen = b & MASK24, (b >> 24) & 0xFF
        n = min(alen, blen)
        left = bytes(self.memory[aaddr : aaddr + n])
        right = bytes(self.memory[baddr : baddr + n])
        if left < right:
            self.cc = 1
        elif left > right:
            self.cc = 2
        else:
            self.cc = 0 if alen == blen else (1 if alen < blen else 2)
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
        s = a - b
        self.r[i.r1] = s & MASK32
        self._logical_add_cc(s, b > a)
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
        product = s32(self.r[i.r1]) * s32(self.fetch_u32(self._rx_addr(i), i.address))
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
        self.r[i.r1] = quotient & MASK32
        self.r[i.r1 + 1] = remainder & MASK32
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
        if not (-2**31 <= value < 2**31):
            raise FixedPointOverflow("CVB value out of range", i.address)
        self.r[i.r1] = value & MASK32
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
            self.r[i.r1] = (i.address + i.length) & MASK32
        self.psw.ia = target

    def op_BCT(self, i: Instruction) -> None:
        self.r[i.r1] = (self.r[i.r1] - 1) & MASK32
        if self.r[i.r1] != 0:
            self.psw.ia = self._rx_addr(i)
        else:
            self.psw.ia = wrap24(i.address + i.length)

    def op_EX(self, i: Instruction) -> None:
        subject_addr = self._rx_addr(i)
        try:
            subject = decode(self.memory, subject_addr)
        except ValueError as exc:
            raise OperationException(str(exc), i.address) from None
        if subject.length < 2:
            raise SpecificationException("EX of a one-byte subject", i.address)
        # OR the low byte of R1 into the second byte of the subject instruction
        patched = bytearray(subject.raw)
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
        self.r[i.r1] = self._rx_addr(i)
        self.psw.ia = wrap24(i.address + i.length)

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
        n = self._shift_amount(i)
        before = s32(self.r[i.r1])
        result = (before << n) & MASK32
        self.r[i.r1] = result
        self._shift_arith_cc(result, before)
        # Overflow when a bit shifted out of the sign position differs from the
        # new sign: equivalently, when an arithmetic right shift by the same
        # amount does not restore the original value.
        if n and (s32(result) >> n) != before:
            self.cc = 3
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
        n = self._shift_amount(i)
        before = s64(self._double(i))
        result = (before << n) & 0xFFFF_FFFF_FFFF_FFFF
        self.r[i.r1] = (result >> 32) & MASK32
        self.r[i.r1 + 1] = result & MASK32
        v = s64(result)
        self.cc = 0 if v == 0 else (1 if v < 0 else 2)
        if n and (s64(result) >> n) != before:
            self.cc = 3
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
        addr = self._rs_addr(i)
        offsets = self._mask_bytes(i.r3)
        if not offsets:
            raise SpecificationException("CLM zero mask", i.address)
        left = [(self.r[i.r1] >> (24 - 8 * k)) & 0xFF for k in offsets]
        right = [self.fetch_byte(addr + k, i.address) for k in offsets]
        if left == right:
            self.cc = 0
        elif left < right:
            self.cc = 1
        else:
            self.cc = 2
        self.psw.ia = wrap24(i.address + i.length)

    def op_STCM(self, i: Instruction) -> None:
        addr = self._rs_addr(i)
        for k in self._mask_bytes(i.r3):
            self.store_byte(addr + k, (self.r[i.r1] >> (24 - 8 * k)) & 0xFF, i.address)
        self.psw.ia = wrap24(i.address + i.length)

    def op_ICM(self, i: Instruction) -> None:
        addr = self._rs_addr(i)
        offsets = self._mask_bytes(i.r3)
        if not offsets:
            raise SpecificationException("ICM zero mask", i.address)
        value = 0
        first_one = None
        for k in range(4):
            if k in offsets:
                byte = self.fetch_byte(addr + k, i.address)
                value |= byte << (24 - 8 * k)
                if first_one is None and byte & 0x80:
                    first_one = 1
        self.r[i.r1] = value
        if value == 0:
            self.cc = 0
        else:
            self.cc = 1 if first_one else 2
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
        addr = self._si_addr(i)
        byte = self.fetch_byte(addr, i.address)
        self.store_byte(addr, 0xFF, i.address)
        self.cc = 0 if byte == 0 else 1
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
        a1 = self.addr(i.b1, i.d1, 0, i.address)
        a2 = self.addr(i.b2, i.d2, 0, i.address)
        return a1, a2

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
        dest, src = self._ss_addrs(i)
        for k in range(i.l + 1):
            self.memory[dest + k] = (self.memory[dest + k] & 0x0F) | (self.memory[src + k] & 0xF0)
        self.psw.ia = wrap24(i.address + i.length)

    def op_MVZ(self, i: Instruction) -> None:
        dest, src = self._ss_addrs(i)
        for k in range(i.l + 1):
            self.memory[dest + k] = (self.memory[dest + k] & 0xF0) | (self.memory[src + k] & 0x0F)
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
        dest, table = self._ss_addrs(i)
        for k in range(i.l + 1):
            function = self.memory[table + self.memory[dest + k]]
            if function:
                self.r[1] = function
                self.r[2] = dest + k
                self.cc = 1 if k == 0 else 2
                self.psw.ia = wrap24(i.address + i.length)
                return
        self.r[2] = dest
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
        n_src = i.l + 1
        n_dst = i.l + 1

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

        Each result byte carries 0xF in its zone, except the low-order byte,
        whose high nibble receives the sign.
        """
        dest, src = self._ss_addrs(i)
        n_src = i.l + 1
        n_dst = i.l + 1

        data = bytes(self.memory[src : src + n_src])
        nibbles = []
        for byte in data:
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
        raise PrivilegedOperation(i.name, i.address)

    op_ED = _privileged_ss
    op_EDMK = _privileged_ss
    op_SRP = _privileged_ss
    op_MVO = _privileged_ss
    op_ZAP = _privileged_ss
    op_CP = _privileged_ss
    op_AP = _privileged_ss
    op_SP = _privileged_ss
    op_MP = _privileged_ss
    op_DP = _privileged_ss

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
        r1=ins.r1, r2=ins.r2, r3=ins.r3, i2=ins.i2, l=ins.l, raw=ins.raw,
    )
