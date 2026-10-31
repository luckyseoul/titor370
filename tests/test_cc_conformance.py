"""Condition-code conformance, from Appendix E of the Principles of Operation.

Appendix E (GA22-7000-4, printed p. 275) lists, for every instruction, which of
the four condition codes it can produce. That is an oracle written by IBM rather
than by this project, and it catches a class of bug that per-instruction tests
miss: an instruction that produces a condition code it can never legally have.

`SUBTRACT LOGICAL` is the example that motivated this file. Its CC-0 column is
blank -- "a zero difference cannot be obtained without a carry out of the sign
position" (p. 144) -- so CC 0 is unreachable. The earlier implementation
produced it, and every hand-written test agreed with the implementation because
both were written from the same misreading.

Every implemented instruction must therefore either appear in CC_SETS with the
codes IBM allows, or appear in CC_UNCHANGED. The test sweeps each instruction
over edge-case operands and fails if an observed code is outside the set.
"""

from __future__ import annotations

import pytest

from titor370.cpu import (
    IMPLEMENTED,
    CPU,
    Halt,
    MEMSIZE,
    ProgramCheck,
    S370Error,
    SupervisorCall,
)
from titor370.isa import OPCODES, Fmt, Instruction, encode

# One condition code per column of Appendix E, p. 275. The four columns are
# CC 0, 1, 2 and 3; a dash in the manual means that code cannot occur.
CC_SETS: dict[str, frozenset[int]] = {
    # General instructions
    "AR": frozenset({0, 1, 2, 3}), "A": frozenset({0, 1, 2, 3}), "AH": frozenset({0, 1, 2, 3}),
    "ALR": frozenset({0, 1, 2, 3}), "AL": frozenset({0, 1, 2, 3}),
    "NR": frozenset({0, 1}), "N": frozenset({0, 1}), "NC": frozenset({0, 1}),
    "NI": frozenset({0, 1}),
    "CR": frozenset({0, 1, 2}), "C": frozenset({0, 1, 2}), "CH": frozenset({0, 1, 2}),
    "CS": frozenset({0, 1}), "CDS": frozenset({0, 1}),
    "CLR": frozenset({0, 1, 2}), "CL": frozenset({0, 1, 2}),
    "CLM": frozenset({0, 1, 2}), "CLCL": frozenset({0, 1, 2}),
    # COMPARE LOGICAL (immediate) sits in the COMPARE LOGICAL row.
    "CLI": frozenset({0, 1, 2}),
    "CLC": frozenset({0, 1, 2}),
    "XR": frozenset({0, 1}), "X": frozenset({0, 1}), "XC": frozenset({0, 1}),
    "XI": frozenset({0, 1}),
    "ICM": frozenset({0, 1, 2}),
    "LTR": frozenset({0, 1, 2}),
    "LCR": frozenset({0, 1, 2, 3}),
    "LNR": frozenset({0, 1}),
    "LPR": frozenset({0, 2, 3}),
    "MVCL": frozenset({0, 1, 2, 3}),
    "OR": frozenset({0, 1}), "O": frozenset({0, 1}), "OC": frozenset({0, 1}),
    "OI": frozenset({0, 1}),
    "SLDA": frozenset({0, 1, 2, 3}),
    "SLA": frozenset({0, 1, 2, 3}),
    "SRDA": frozenset({0, 1, 2}),
    "SRA": frozenset({0, 1, 2}),
    "SR": frozenset({0, 1, 2, 3}), "S": frozenset({0, 1, 2, 3}), "SH": frozenset({0, 1, 2, 3}),
    # CC 0 is absent from the manual's row.
    "SLR": frozenset({1, 2, 3}), "SL": frozenset({1, 2, 3}),
    "TS": frozenset({0, 1}),
    # Decimal arithmetic (Appendix E, Decimal Instructions)
    "AP": frozenset({0, 1, 2, 3}), "SP": frozenset({0, 1, 2, 3}),
    "ZAP": frozenset({0, 1, 2, 3}), "CP": frozenset({0, 1, 2}),
    "TM": frozenset({0, 1, 3}),
    "TRT": frozenset({0, 1, 2}),
}

# Instructions whose condition code the manual does not set (no "C" in Appendix C).
CC_UNCHANGED: frozenset[str] = frozenset(
    """
    LR L ST LA LH STH IC STC MVC MVN MVZ TR PACK UNPK CVD CVB
    LM STM STCM
    BAL BALR BCT BCTR BC BCR BXH BXLE
    DIAG
    MR M D DR MH
    SLL SRL SLDL SRDL
    MVI
    MP DP
    """.split()
)

# EX takes its condition code from the subject instruction, so any code is legal.
CC_FROM_SUBJECT = frozenset({"EX"})

# These never complete in problem state, so they never reach a condition code.
ALWAYS_RAISES = frozenset({"LPSW", "LRA", "SSM", "SVC"})

EDGES = [
    0x00000000,
    0x00000001,
    0xFFFFFFFF,   # -1
    0x80000000,   # INT32_MIN
    0x7FFFFFFF,   # INT32_MAX
    0x00000002,
    0xFFFFFFFE,   # -2
    0x80000001,
]

BYTE_PATTERNS = [
    bytes(8),
    bytes([0xFF] * 8),
    bytes(range(8)),
    bytes([0x00, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
    bytes([0x01, 0x23, 0x45, 0x67, 0x89, 0xAB, 0xCD, 0xEF]),
]


def _base() -> CPU:
    cpu = CPU()
    cpu.load(0x1000, b"\x00" * 256)
    return cpu


def _run(cpu: CPU, name: str, fields, regs=None, mem=None):
    """Execute one instruction; return its condition code, or None if it raised."""
    data = encode(name, *fields)
    cpu.load(0x1000, data)
    cpu.r[:] = [0] * 16
    if regs:
        for index, value in regs.items():
            cpu.r[index] = value & 0xFFFFFFFF
    if mem:
        for addr, blob in mem.items():
            cpu.write(addr, blob)
    cpu.psw.ia = 0x1000
    cpu.halted = False
    try:
        cpu.step()
    except (ProgramCheck, SupervisorCall, Halt, S370Error):
        return None
    return cpu.cc


def _sweep_rr(name: str) -> set[int]:
    seen = set()
    for a in EDGES:
        for b in EDGES:
            cc = _run(_base(), name, (1, 2), regs={1: a, 2: b})
            if cc is not None:
                seen.add(cc)
    return seen


def _sweep_rx(name: str) -> set[int]:
    seen = set()
    for a in EDGES:
        for value in EDGES:
            cpu = _base()
            cc = _run(cpu, name, (1, 0, 0, 0x200), regs={1: a},
                      mem={0x200: value.to_bytes(4, "big")})
            if cc is not None:
                seen.add(cc)
    return seen


def _sweep_shift(name: str, double: bool) -> set[int]:
    seen = set()
    # A zero operand yields CC 0 for the arithmetic shifts.
    cc = _run(_base(), name, (2, 0, 0, 0), regs={2: 0, 3: 0})
    if cc is not None:
        seen.add(cc)
    for value in EDGES:
        for n in (0, 1, 2, 30, 31, 32, 40, 63):
            regs = {2: value, 3: value ^ 0xFFFFFFFF} if double else {1: value}
            cc = _run(_base(), name, (2, 0, 0, n) if double else (1, 0, 0, n), regs=regs)
            if cc is not None:
                seen.add(cc)
    return seen


def _sweep_ss(name: str) -> set[int]:
    seen = set()
    if name == "ZAP":
        # ZAP can only overflow when the source is longer than the target.
        cpu = _base()
        cpu.write(0x200, bytes(2))
        cpu.write(0x300, _pack(12345, 4))
        cc = _run(cpu, name, (1, 3, 0, 0x200, 0, 0x300))
        if cc is not None:
            seen.add(cc)
    # Packed decimal requires valid sign nibbles; sweep values, not random bytes.
    if name in ("AP", "SP", "ZAP", "CP", "MP", "DP"):
        for left in (0, 1, -1, 1234, -1234, 999, -999, 9999999, -9999999):
            for right in (0, 1, -1, 7, -7, 100, 9999999):
                l1 = 4 if name in ("AP", "SP", "ZAP", "CP") else 6
                l2 = 2 if name in ("AP", "SP", "ZAP", "CP") else 2
                first = _pack(left, l1)
                second = _pack(right, l2)
                cpu = _base()
                cpu.write(0x200, first)
                cpu.write(0x300, second)
                cc = _run(cpu, name, (l1 - 1, l2 - 1, 0, 0x200, 0, 0x300))
                if cc is not None:
                    seen.add(cc)
        return seen
    if name == "TRT":
        # A table that tags only the last byte of the field gives CC 2; one
        # that tags an earlier byte gives CC 1.
        # Tagging 7 with the field 00..07 hits on the last byte (CC 2);
        # tagging 3 with 03..00 hits on the first of several (CC 1).
        cases = [(7, bytes([0, 0, 0, 0, 0, 0, 0, 7])),
                 (3, bytes([3, 0, 0, 0, 0, 0, 0, 0])),
                 (9, bytes([0, 0, 0, 0, 0, 0, 0, 0]))]   # no hit at all: CC 0
        for tagged, field in cases:
            table = bytearray(256)
            table[tagged] = 0x0F
            cpu = _base()
            cpu.write(0x200, field)
            cpu.write(0x300, bytes(table))
            cc = _run(cpu, name, (7, 0, 0x200, 0, 0x300))
            if cc is not None:
                seen.add(cc)
    for pattern in BYTE_PATTERNS:
        for other in BYTE_PATTERNS:
            cpu = _base()
            cpu.write(0x200, pattern)
            cpu.write(0x300, other)
            cc = _run(cpu, name, (7, 0, 0x200, 0, 0x300))
            if cc is not None:
                seen.add(cc)
    return seen


def _sweep_si(name: str) -> set[int]:
    seen = set()
    for byte in (0x00, 0xFF, 0x80, 0x01, 0x0F, 0xF0, 0x7F):
        for immediate in (0x00, 0xFF, 0x80, 0x01, 0x0F):
            cpu = _base()
            cpu.write(0x200, bytes([byte]))
            cc = _run(cpu, name, (immediate, 0, 0x200))
            if cc is not None:
                seen.add(cc)
    return seen


def _pack(value: int, length: int) -> bytes:
    """Packed decimal, encoded independently of the emulator."""
    digits = f"{abs(value):0{2 * length - 1}d}"[-max(1, 2 * length - 1):]
    nibbles = [int(d) for d in digits] + [0xD if value < 0 else 0xC]
    out = bytearray(length)
    for index in range(length):
        out[index] = (nibbles[2 * index] << 4) | nibbles[2 * index + 1]
    return bytes(out)


def _sweep_long(name: str) -> set[int]:
    """MVCL and CLCL: even-odd register pairs with 24-bit lengths."""
    seen = set()
    # MVCL condition code 3 needs a destructive overlap: the destination starts
    # inside the source field, to its right.
    cpu = _base()
    cpu.write(0x300, bytes(16))
    cc = _run(cpu, name, (2, 4), regs={2: 0x302, 3: 8, 4: 0x300, 5: 8})
    if cc is not None:
        seen.add(cc)
    for dlen in (0, 1, 4, 8):
        for slen in (0, 1, 4, 8):
            for pad in (0x00, 0x40, 0xFF):
                for addr in (0x200, 0x202, 0x204):
                    cpu = _base()
                    cpu.write(0x200, bytes(24))
                    cpu.write(0x300, bytes(24))
                    # Registers must be passed in: _run resets the file.
                    regs = {2: addr, 3: dlen, 4: 0x300, 5: (pad << 24) | slen}
                    cc = _run(cpu, name, (2, 4), regs=regs)
                    if cc is not None:
                        seen.add(cc)
    return seen


def _sweep_s(name: str) -> set[int]:
    """Single-operand S-format instructions such as TEST AND SET."""
    seen = set()
    for byte in (0x00, 0x80, 0x01, 0xFF, 0x7F):
        for preset in (0, 1, 2, 3):
            cpu = _base()
            cpu.write(0x200, bytes([byte]))
            cpu.r[:] = [0] * 16
            cpu.load(0x1000, encode(name, 0, 0x200))
            cpu.psw.cc = preset
            cpu.psw.ia = 0x1000
            cpu.halted = False
            try:
                cpu.step()
            except (ProgramCheck, SupervisorCall, Halt, S370Error):
                continue
            seen.add(cpu.cc)
    return seen


def _sweep(name: str) -> set[int]:
    if name in ("MVCL", "CLCL"):
        return _sweep_long(name)
    fmt = OPCODES[name][1]
    if fmt is Fmt.RR:
        if name in ("LCR", "LNR", "LPR", "LTR"):
            return _sweep_rr(name)
        return _sweep_rr(name)
    if fmt is Fmt.RX:
        return _sweep_rx(name)
    if fmt is Fmt.RS:
        if name in ("SLDA", "SRDA"):
            return _sweep_shift(name, double=True)
        if name in ("SLA", "SRA"):
            return _sweep_shift(name, double=False)
        return _sweep_rr_rs(name)
    if fmt is Fmt.SI:
        return _sweep_si(name)
    if fmt is Fmt.SS:
        return _sweep_ss(name)
    if fmt is Fmt.S:
        return _sweep_s(name)
    return set()


def _sweep_rr_rs(name: str) -> set[int]:
    """RS instructions that do not shift: BXH/BXLE, CS, CDS, LM/STM, CLM."""
    seen = set()
    for a in EDGES:
        for b in EDGES:
            for c in (1, 2, 3):
                regs = {1: a, 2: b, 3: c, 4: c, 5: b}
                # Equal in one pass (CC 0) and unequal in the other (CC 1).
                mem = {0x200: a.to_bytes(4, "big") + b.to_bytes(4, "big")}
                cc = _run(_base(), name, (2, 3, 0, 0x200), regs=regs, mem=mem)
                if cc is not None:
                    seen.add(cc)
    return seen


@pytest.mark.parametrize("name", sorted(CC_SETS))
def test_condition_codes_stay_within_the_appendix_e_set(name):
    observed = _sweep(name)
    allowed = CC_SETS[name]
    # A sweep that never completed an instruction would pass vacuously; make
    # sure this one actually observed a condition code.
    assert observed, f"{name} never completed during the sweep -- the test proved nothing"
    unexpected = observed - allowed
    assert not unexpected, (
        f"{name} produced condition code(s) {sorted(unexpected)}; "
        f"Appendix E allows only {sorted(allowed)}"
    )
    # The other half of the check: if the sweep cannot reach a code the manual
    # allows, the test for that code is not being exercised. Treat that as a
    # test-quality failure rather than letting it pass quietly.
    unreached = allowed - observed
    assert not unreached, (
        f"{name} never produced condition code(s) {sorted(unreached)}, which "
        f"Appendix E allows -- the sweep is too weak to test them"
    )


@pytest.mark.parametrize("name", sorted(ALWAYS_RAISES))
def test_privileged_and_supervisor_calls_raise(name):
    fmt = OPCODES[name][1]
    fields = {
        Fmt.RR: (0, 0),
        Fmt.RX: (0, 0, 0, 0x200),
        Fmt.RS: (0, 0, 0, 0x200),
        Fmt.SI: (0, 0, 0x200),
        Fmt.SS: (0, 0, 0x200, 0, 0x300),
        Fmt.S: (0, 0x200),
    }[fmt]
    cpu = _base()
    cpu.load(0x1000, encode(name, *fields))
    cpu.psw.ia = 0x1000
    cpu.halted = False
    with pytest.raises((ProgramCheck, SupervisorCall)):
        cpu.step()


@pytest.mark.parametrize("name", sorted(CC_UNCHANGED))
def test_instructions_without_condition_codes_leave_cc_alone(name):
    """Every instruction the manual does not mark 'C' must preserve CC."""
    perm = _run_preserving(name)
    assert perm is not None, f"{name} could not be executed in any preset state"


def _run_preserving(name: str) -> bool | None:
    fmt = OPCODES[name][1]
    survived_any = False
    for preset in (0, 1, 2, 3):
        cpu = _base()
        cpu.r[:] = [0] * 16
        cpu.r[1] = 0x1000
        cpu.r[2] = 0x1000
        cpu.r[3] = 0x1000
        # Valid packed decimal where an instruction reads packed data, so that
        # CVB completes rather than raising on a bad sign nibble.
        cpu.write(0x200, bytes.fromhex("000000000000000C"))
        cpu.write(0x300, bytes(16))
        if fmt is Fmt.RR:
            fields = (2, 3)      # even, as MR/DR require
        elif fmt is Fmt.RX:
            fields = (2, 0, 0, 0x200)
        elif fmt is Fmt.RS:
            fields = (2, 3, 0, 0x200)   # even, as the double shifts require
        elif fmt is Fmt.SI:
            fields = (0x00, 0, 0x200)
        elif fmt is Fmt.SS:
            if name in ("MP", "DP"):
                cpu.write(0x200, _pack(100, 5))
                cpu.write(0x300, _pack(3, 1))
                fields = (4, 0, 0, 0x200, 0, 0x300)
            else:
                cpu.write(0x200, _pack(1, 4))
                cpu.write(0x300, _pack(1, 4))
                fields = (3, 3, 0, 0x200, 0, 0x300)
        else:
            fields = (0, 0x200)
        cpu.load(0x1000, encode(name, *fields))
        if name in ("D", "DR"):
            # A dividend small enough that the quotient fits, and a non-zero
            # divisor, so the division completes.
            cpu.r[2] = 0
            cpu.r[3] = 100
            cpu.write(0x200, (7).to_bytes(4, "big"))
            if name == "DR":
                cpu.r[3] = 7
        cpu.psw.cc = preset
        cpu.psw.ia = 0x1000
        cpu.halted = False
        try:
            cpu.step()
        except (ProgramCheck, SupervisorCall, Halt, S370Error):
            continue
        survived_any = True
        assert cpu.cc == preset, (
            f"{name} changed the condition code from {preset} to {cpu.cc}, "
            f"but Appendix C does not mark it as setting CC"
        )
    return survived_any or None


def test_every_implemented_instruction_is_classified():
    """No instruction may escape both tables -- that is how a gap hides."""
    classified = set(CC_SETS) | CC_UNCHANGED | CC_FROM_SUBJECT | ALWAYS_RAISES
    missing = sorted(set(IMPLEMENTED) - classified)
    assert not missing, f"unclassified instructions: {missing}"
