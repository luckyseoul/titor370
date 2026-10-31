"""Coverage bookkeeping.

The README makes claims about what is implemented and what is translated. These
tests make those claims checkable, so documentation cannot drift away from the
code without a test failing.
"""

from __future__ import annotations

import re

from titor370.cpu import IMPLEMENTED, UNIMPLEMENTED
from titor370.isa import OPCODES, Fmt, Instruction
from titor370.translate import emit

# Instructions the interpreter runs but the C translator does not emit.
# Keep in sync with the README's "Limits" section.
NOT_TRANSLATED = {"SVC", "MVCL", "CLCL", "EX", "CDS", "LPSW", "SSM", "LRA"}


def _synthetic(name: str) -> Instruction:
    op, fmt = OPCODES[name]
    return Instruction(
        0x1000, op, name, fmt,
        {Fmt.RR: 2, Fmt.SS: 6}.get(fmt, 4),
        b1=2, d1=0x200, b2=3, d2=0x210,
        r1=2, r2=4, r3=6, i2=0x40, l1=3, l2=3,
        raw=bytes([op, 0x24, 0x02, 0x00, 0x03, 0x10]),
    )


def test_every_implemented_instruction_has_an_opcode():
    missing = [name for name in IMPLEMENTED if name not in OPCODES]
    assert missing == []


def test_no_instruction_is_both_implemented_and_unimplemented():
    overlap = set(IMPLEMENTED) & set(UNIMPLEMENTED)
    assert overlap == set()


def test_translator_covers_everything_but_the_documented_exceptions():
    untranslated = set()
    for name in IMPLEMENTED:
        if name not in OPCODES:
            continue
        emitted = "\n".join(emit(_synthetic(name), {}))
        # Only a static "not translated" counts; runtime guards such as the
        # divide-by-zero check are not coverage gaps.
        if re.search(r'unimplemented\([^,]+, "' + re.escape(name) + r' is not translated"', emitted):
            untranslated.add(name)
    assert untranslated == NOT_TRANSLATED, (
        f"coverage changed: translator does not handle {sorted(untranslated - NOT_TRANSLATED)}, "
        f"or now handles {sorted(NOT_TRANSLATED - untranslated)}; update the README"
    )


def test_readme_documents_the_untranslated_set():
    readme = (__import__("pathlib").Path(__file__).resolve().parent.parent / "README.md").read_text()
    expected = "SVC, MVCL, CLCL, EX, CDS, LPSW, SSM"
    assert expected in readme, f"README must list the untranslated instructions as {expected!r}"
