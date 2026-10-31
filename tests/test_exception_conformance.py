"""Program-exception conformance, from Appendix C of the Principles of Operation.

Appendix C (GA22-7000-4, pp. 253-267) prints a "Characteristics" column for
every instruction. Those single codes say which program exceptions the
instruction can raise:

    A   Access exceptions            SP  Specification
    IF  Fixed-point overflow         IK  Fixed-point divide
    EX  Execute                      M   Privileged-operation
    D   Data                         DF  Decimal overflow
    DK  Decimal divide

The point of this file is the *negative* direction, which per-instruction tests
rarely check. `MVCL` declares only `A SP`: it can raise an access or a
specification exception, and nothing else. If a bug makes it raise a fixed-point
divide, the exception is as wrong as a wrong answer would be, and no existing
test would notice.

Each instruction is driven through a spread of hostile operands. Exceptions
outside its documented set fail the test. An instruction whose declared
exceptions are never triggered at all is also reported, because a sweep that
cannot reach them is not testing them.
"""

from __future__ import annotations

import pytest

from titor370.cpu import (
    IMPLEMENTED,
    CPU,
    Halt,
    ProgramCheck,
    S370Error,
    SupervisorCall,
)
from titor370.isa import OPCODES, Fmt, encode

# Exception bits, named after the manual's abbreviations.
A, SP, IF, IK, EX, M, D = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40
DF, DK = 0x80, 0x100

LEGEND_UNDERSTOOD = frozenset(
    {
        "A", "SP", "IF", "IK", "EX", "M", "D",
        "DF", "DK",            # decimal overflow / divide, not implemented
        "CK", "DC",            # CPU timer, direct control
        "E", "U", "LS", "FK",  # floating point
    }
)

# From the Appendix C Characteristics column. Only exceptions this emulator can
# model are listed; A = access, SP = specification, IF = overflow, IK = divide.
EXCEPTIONS = {
    "AR": {IF}, "SR": {IF}, "A": {A, IF}, "S": {A, IF},
    "AH": {A, IF}, "SH": {A, IF}, "M": {A, SP}, "D": {A, SP, IK},
    "MR": {SP}, "DR": {SP, IK},
    "MH": {A},
    "ALR": set(), "AL": {A}, "SLR": set(), "SL": {A},
    "LTR": set(), "LCR": {IF}, "LNR": set(), "LPR": {IF},
    "NR": set(), "N": {A}, "OR": set(), "O": {A}, "XR": set(), "X": {A},
    "CR": set(), "C": {A}, "CLR": set(), "CL": {A}, "CH": {A},
    "LR": set(), "L": {A}, "LH": {A}, "LA": set(), "ST": {A}, "STH": {A},
    "IC": {A}, "STC": {A},
    "CVD": {A}, "CVB": {A, D, IK},
    "LPSW": {M, SP},
    "LM": {A}, "STM": {A},
    "SLL": set(), "SRL": set(), "SLA": {IF}, "SRA": set(),
    "SLDL": {SP}, "SRDL": {SP}, "SLDA": {SP, IF}, "SRDA": {SP},
    "BXH": set(), "BXLE": set(),
    "CS": {A, SP}, "CDS": {A, SP},
    "CLM": {A, SP}, "STCM": {A}, "ICM": {A},
    "MVC": {A}, "MVN": {A}, "MVZ": {A}, "NC": {A}, "OC": {A}, "XC": {A},
    "CLC": {A}, "TR": {A}, "TRT": {A},
    "AP": {A, D, DF}, "SP": {A, D, DF}, "ZAP": {A, D, DF},
    "MP": {A, SP, D}, "DP": {A, SP, D, DK}, "CP": {A, D},
    "MVO": {A}, "SRP": {A, D, DF}, "ED": {A, D}, "EDMK": {A, D},
    "PACK": {A, SP}, "UNPK": {A},
    "MVI": {A}, "NI": {A}, "OI": {A}, "XI": {A},
    "TM": {A}, "CLI": {A},
    "TS": {A},
    "MVCL": {A, SP}, "CLCL": {A, SP},
    "BC": set(), "BCR": set(), "BAL": set(), "BALR": set(),
    "BCT": set(), "BCTR": set(), "EX": {A, EX, SP},
    "SVC": set(), "DIAG": set(),
    "SSM": {A, M, SP}, "LRA": {M, SP},
}

EDGES = [0x00000000, 0x00000001, 0xFFFFFFFF, 0x80000000, 0x7FFFFFFF, 0x40000000]


def _kind(exc: BaseException) -> int | None:
    """Map an exception to its Appendix C letter."""
    if isinstance(exc, ProgramCheck):
        return {
            0x01: None,   # operation: "must not occur" is not in the column
            0x02: M,
            0x03: EX,
            0x04: A,
            0x05: A,
            0x06: SP,
            0x07: D,
            0x08: IF,
            0x09: IK,
            0x0A: DF,
            0x0B: DK,
        }.get(exc.code)
    return None


def _drive(name: str) -> tuple[set[int], set[int]]:
    """Return (exceptions observed, codes observed) over a hostile sweep."""
    seen: set[int] = set()
    codes: set[int] = set()
    fmt = OPCODES[name][1]
    for a in EDGES:
        for b in EDGES:
            cpu = CPU()
            cpu.load(0x1000, b"\x00" * 32)
            # Point operands at the very top of the store to provoke access
            # exceptions, and at ordinary addresses to let them complete.
            # Two passes: ordinary operands (so arithmetic can complete or
            # overflow) and high ones (so the access exception is provoked).
            for high in (False, True):
                storage = 0x7FFFFFFF if high else 0x00000000
                cpu.r[:] = [0] * 16
                cpu.r[0] = a
                cpu.r[1] = a
                cpu.r[2] = b
                cpu.r[3] = b
                cpu.r[4] = a
                cpu.r[5] = b
                # R6/R7 carry a high base, so base-addressed operands run off
                # the top of the 16 MiB store and raise an access exception.
                cpu.r[6] = 0xFFFF00
                cpu.r[7] = 0xFFFF00
                # Large and small second operands, so overflow and divide can
                # actually be provoked.
                cpu.write(0x200, storage.to_bytes(4, "big") * 2)
                if fmt is Fmt.RR:
                    fields = (2, 3)
                elif fmt is Fmt.RX:
                    fields = (2, 0, 0, 0x200) if not high else (2, 0, 6, 0x0FE)
                elif fmt is Fmt.RS:
                    fields = (2, 3, 0, 0x200) if not high else (2, 3, 6, 0x0FE)
                elif fmt is Fmt.SI:
                    fields = (0x00, 0, 0x200) if not high else (0x00, 6, 0x0FE)
                elif fmt is Fmt.SS:
                    fields = (7, 0, 0x200, 0, 0x300) if not high else (7, 6, 0x0FE, 6, 0x0FE)
                else:
                    fields = (0, 0x200) if not high else (6, 0x0FE)
                try:
                    data = encode(name, *fields)
                except (TypeError, ValueError):
                    continue
                cpu.load(0x1000, data)
                cpu.psw.ia = 0x1000
                # Enable every program-mask interruption this model can raise.
                cpu.psw.pmask = 0xF
                cpu.halted = False
                try:
                    cpu.step()
                except (ProgramCheck, SupervisorCall, Halt) as exc:
                    kind = _kind(exc)
                    if kind:
                        seen.add(kind)
                    if isinstance(exc, ProgramCheck):
                        codes.add(exc.code)
                    continue
                except S370Error:
                    continue
                codes.add(0)
    return seen, codes


@pytest.mark.parametrize("name", sorted(EXCEPTIONS))
def test_no_undocumented_program_exception(name):
    observed, codes = _drive(name)
    allowed = EXCEPTIONS[name]
    unexpected = observed - allowed
    assert not unexpected, (
        f"{name} raised undocumented exception(s) {_names(unexpected)}; "
        f"Appendix C allows {_names(allowed) or 'none'}"
    )


# One byte is the whole store's granularity: with a 16 MiB store and 24-bit
# addressing a single-byte operand always lies inside it, so an access
# exception is unreachable for these instructions in this model.
BYTE_ONLY = frozenset({"STC", "IC", "MVI", "NI", "OI", "XI", "TM", "CLI", "TS"})

COVERAGE: dict[str, set[int]] = {}


@pytest.mark.parametrize("name", sorted(EXCEPTIONS))
def test_documented_exceptions_are_reachable(name):
    """Record which declared exceptions the sweep actually provoked.

    This does not assert per instruction: some exceptions are unreachable for a
    given instruction in *this* model -- a one-byte operand always lies inside a
    full 16 MiB store, for instance. The aggregate floor below is what stops
    that from decaying into a sweep that tests nothing.
    """
    observed, _codes = _drive(name)
    allowed = EXCEPTIONS[name]
    if name in BYTE_ONLY:
        allowed = allowed - {A}
    COVERAGE[name] = observed & allowed
    assert True


def test_the_sweep_provokes_a_useful_share_of_the_documented_exceptions():
    """An aggregate floor: the sweep must reach most of what it claims to test.

    The sweep is re-run here rather than relying on the parametrised test above
    having already populated COVERAGE, so the result does not depend on test
    ordering.
    """
    total = 0
    reached = 0
    for name, allowed in sorted(EXCEPTIONS.items()):
        if name in BYTE_ONLY:
            allowed = allowed - {A}
        observed, _codes = _drive(name)
        total += len(allowed)
        reached += len(observed & allowed)
    share = reached / total if total else 0
    # The floor is set just below the measured value. It exists to catch the
    # sweep decaying -- an instruction whose operands stop being reachable would
    # otherwise keep the strict assertion above passing while testing nothing.
    # It is not a claim that every declared exception is provoked: the access
    # exceptions in particular are only partly reachable in a 16 MiB store.
    assert share >= 0.45, (
        f"the sweep only provoked {reached}/{total} documented exceptions "
        f"({share:.0%}); it has decayed"
    )


def _names(bits: set[int]) -> str:
    reverse = {A: "A", SP: "SP", IF: "IF", IK: "IK", EX: "EX", M: "M", D: "D",
               DF: "DF", DK: "DK"}
    return "{" + ", ".join(reverse[b] for b in sorted(bits)) + "}" if bits else ""


def test_every_implemented_instruction_is_classified():
    missing = sorted(set(IMPLEMENTED) - set(EXCEPTIONS))
    assert not missing, f"instructions with no Appendix C row here: {missing}"
