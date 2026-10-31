# titor370

**The machine John Titor described, built so it can be checked.**

In 2000, the "John Titor" internet persona claimed the IBM 5100 had an undocumented
ability to *"emulate IBM System/370 mainframes and translate between their machine code and
more modern languages."*

The first half of that is false, and [the documentation
says so](https://github.com/luckyseoul/ibm-5100-docs): the 5100 ran IBM's System/370 *APL
implementation* on a microcoded interpreter; it never executed S/370 instructions. The second
half is the interesting part — nobody has ever had software that translates S/370 machine code
into a modern language, and checks that the translation matches the machine it came from.

This repository is that software. It is a real System/370 machine, written to be verified
rather than admired.

```    S/370 machine code  ──interpreter──►  registers, CC, storage
                          ╲
                           ╲──translator──►  C program ──gcc──►  the same registers, CC, storage
```

Both paths run the same program. The test suite asserts they agree, register for register and
byte for byte. That is the whole idea.

---

## What is here

| Component | What it does |
|---|---|
| `titor370/isa.py` | Instruction formats and opcodes, transcribed from IBM GA22-7000-4 |
| `titor370/cpu.py` | The interpreter: 16 general registers, EC-mode PSW, 16 MiB of 24-bit storage |
| `titor370/asm.py` | Two-pass assembler with labels, `USING`, `DC`/`DS`, EBCDIC constants, branch aliases |
| `titor370/disasm.py` | Disassembler |
| `titor370/translate.py` | Static binary translator: S/370 → C |
| `titor370/cli.py` | `asm`, `dis`, `run`, `translate`, `compare` |

Where every opcode and rule came from: **[SOURCES.md](SOURCES.md)** — which edition of the
Principles of Operation, which printed pages, and which rules were read off page images rather
than OCR.

## Show me

```bash
$ python3 -m titor370.cli run examples/sum.s370 --memory 1036:4
registers
  R0  00000000  R1  00000096  R2  00001036  R3  00000000
  ...
CC 2   IA 00101E   steps 21

$ python3 -m titor370.cli compare examples/sum.s370
MATCH
  54 bytes of S/370, 21 instructions
  registers, condition code and 64 bytes of storage agree
```

`compare` assembles the program, runs it on the interpreter, translates it to C, compiles the C
with `cc`, runs that, and diffs the two machine states.

## The translation

This is real machine code doing real work, turned into C:

```c
L_00100E: /* 00100E  A 5A102000 */
    { int64_t t = (int64_t)(int32_t)R[1] + (int64_t)(int32_t)ld32(ea(2u, 0u, 0u));
      R[1] = (uint32_t)t;
      if (t > 2147483647LL || t < -2147483648LL) CC = 3; else CC = cc_of(t); }
    IA = 0x001012u;
L_001016: /* 001016  BCT 4630C00C */
    R[3] = R[3] - 1u;
    if (R[3]) goto L_00100E;
    IA = 0x00101Au;
```

Two details worth noticing, because they are where naive translators go wrong:

- **ADD computes in 64 bits and truncates**, so 32-bit wraparound happens exactly once — and
  overflow sets condition code 3 rather than being silently lost.
- **BCT decrements and then branches**, so the loop-carried value is correct on both paths.

Computed branches (`BCR`, `BALR r1,r2` with `r2≠0`) become a `switch` on the target address,
which is what any ahead-of-time translator does when the target is not statically known.

## Instruction set

Implemented and tested: `AR SR ALR SLR LR LTR LNR LPR LCR NR OR XR CR CLR MR DR`,
`BALR BCTR BCR SVC MVCL CLCL`, `L ST LA LH STH IC STC A S AL SL N O X C CL M D AH SH CH MH
CVD CVB`, `EX BAL BCT BC LRA`, `SLL SRL SLA SRA SLDL SRDL SLDA SRDA STM LM BXH BXLE CS CDS
CLM STCM ICM`, `TM MVI NI OI XI CLI`, `MVC MVN MVZ NC OC XC CLC TR TRT PACK UNPK`, `TS DIAG`,
and packed decimal: `AP SP MP DP ZAP CP`.

Deliberately **not** implemented, and they raise a program check rather than pretending:
`SPM SSK ISK WRD RDD STNSM STOSM SIO TIO HIO TCH`; the decimal *editing* and shift
instructions (`ED EDMK SRP MVO`); **all floating point**, which the 5100 did not have either;
`LPSW` and `SSM` (privileged); and DAT.

## Design notes worth knowing

Four rules that are easy to get wrong, and that this implementation gets right:

1. **A base or index field of zero means "not used"** — it does not name register 0. `L 1,4(0,0)`
   loads from absolute address 4 regardless of R0's contents.
2. **`BCR`'s R1 field is a mask, not a register number.** That is why `07 FE` (`BCR 15,14`) is
   the universal return-from-subroutine: mask 15 is unconditional, and R14 holds the address.
3. **`BXH`/`BXLE` compare against an implied register**: "the comparand register address is odd
   and is either one larger than R3 or equal to R3" (GA22-7000-4, p. 122). The increment is
   applied whether or not the branch is taken.
4. **`MVC` propagates on overlap** — it stores as it fetches, so copying `ABCDE` onto itself
   one byte to the right yields `AAAAA`, not a memmove'd result.


## A condition-code oracle from the manual

`tests/test_cc_conformance.py` encodes Appendix E of the Principles of Operation — IBM's own
table of which of the four condition codes each instruction can produce — and checks the
implementation against it.

That table is an oracle written by IBM rather than by this project, and it catches a class of
bug that hand-written tests miss. `SUBTRACT LOGICAL` is the example: its CC-0 column in the
manual is blank, because "a zero difference cannot be obtained without a carry out of the sign
position". An earlier version of this emulator produced CC 0 anyway, and every per-instruction
test agreed with it, because the tests and the code shared the same misreading.

The test sweeps each instruction over edge-case operands and asserts two things:

- no instruction ever produces a condition code outside its documented set, and
- every condition code the manual allows is actually reachable by the sweep.

The second assertion exists because the first is trivially satisfied by a test that never runs
the instruction. When this file was first written, three instructions (`MVCL`, `CLCL`, `TS`)
were passing vacuously for exactly that reason, and four more were not reaching their full set.
The sweep was fixed rather than the assertion relaxed.

To confirm the oracle has teeth, the `SLR` bug was reintroduced deliberately: the test fails,
naming the instruction and the illegal code.

It does **not** catch every kind of error — `TS` produced the wrong condition code for a
different reason (zero-ness instead of the leftmost bit) and stayed inside its legal set {0,1}.
A condition-code oracle bounds the damage; it does not replace reading the page.

## Packed decimal

`AP SP MP DP ZAP CP` are implemented against chapter 7 of the manual: packed fields of 1–16
bytes, sign nibbles 10/12/14/15 for plus and 11/13 for minus, and results always written with
the preferred codes 12 and 13. Digits, lengths and signs are validated, so a malformed field
raises a data exception rather than producing a plausible wrong number.

`tests/test_decimal.py` checks every operation against Python's own integers through helpers
that encode and decode packed fields independently of the emulator.

Two things this found while being written:

- **My program-mask bit assignment was wrong.** Decimal overflow is bit 0, fixed-point overflow
  bit 1; I had used the fixed-point mask for all overflows.
- **`MP` was setting a condition code.** The manual says its condition code is unchanged; the
  condition-code oracle caught it immediately.

The translator does not emit the decimal instructions yet, so `examples/decimal.s370` reports
`SKIP` rather than `MATCH` — stated, not hidden.

## A program-exception oracle

`tests/test_exception_conformance.py` does the same thing for **exceptions**, from the
"Characteristics" column of Appendix C: `MVCL` declares only `A SP`, so if a bug makes it raise
a fixed-point divide, that is as wrong as a wrong answer and nothing else would notice.

The negative direction is asserted strictly — an instruction may never raise an exception its
row does not list. Reachability is reported and held to an aggregate floor rather than asserted
per instruction, because some exceptions are genuinely unreachable here: a one-byte operand
always lies inside a full 16 MiB store, so `TS` cannot take an access exception in this model.

It found two real defects while being written:

- **SS instructions did not bounds-check their operands.** An out-of-range field escaped as a
  Python `IndexError` instead of an addressing exception.
- **Fixed-point overflow never interrupted.** CC 3 was set, but the overflow was never raised
  as a program exception even when the PSW's overflow mask bit was on. Both are now correct.

## An independent review

`REVIEW.md` records a review of this project against Hercules, a mature System/370 emulator,
plus the source of both. It found a dozen genuine misreadings of the Principles of Operation —
among them `MR`'s multiplicand, `DR`'s quotient/remainder order, `MVN`/`MVZ`, `SLR`'s condition
code, `TRT`'s registers, and `SLA`'s sign handling. Every finding was re-checked against
GA22-7000-4 before being accepted; all are fixed, and `REVIEW.md` now ends with a response
table naming the page that settles each one.

That review is the reason to trust the test suite more than the README: the tests and the code
were wrong together, and only an outside oracle could show it.

## Where the encodings come from

Not from memory. IBM GA22-7000-4, *System/370 Principles of Operation*, September 1975 —
contemporary with the 5100 — was downloaded from bitsavers. Its Appendix C, *Instructions
Arranged by Operation Code* (printed pages 262–267), was OCR'd twice and the two passes
cross-checked: 117 opcode/mnemonic pairs agreed, and the 20 that disagreed were all OCR noise
resolved by reading the rendered page images. Semantics for the subtle instructions (`BXH`,
`BCR`, `SLA`, `MVC`, `PACK`, `TRT`) were read from the instruction descriptions, not inferred.

The raw OCR rows are kept in `tools/pop_opcodes_ocr.json` for audit.

## Testing

```
$ python3 -m pytest -q
948 passed, 1 skipped
```

Three kinds of test:

- **Encoding** (`tests/test_isa.py`) — every opcode checked against the PoP table, plus
  encode/decode round-trips for the whole instruction set.
- **Semantics** (`tests/test_cpu.py`) — one hand-computed case per rule: overflow condition
  codes, logical carry vs borrow, truncating division, sign extension, shift overflow, MVC
  propagation, packed-decimal sign nibbles, mask decoding, and so on.
- **Equivalence** (`tests/test_equivalence.py`) — for every example program, the interpreter
  and the compiled C must agree on all 16 registers, the condition code, and the program's
  storage.
- **Oracle** (`tests/test_oracle.py`) — arithmetic and comparison results are recomputed in
  Python from their definitions across every edge-case pair (0, ±1, INT32_MIN/MAX, 0xFFFFFFFF,
  …), plus random 64-bit products and divisions. 320 checks that cannot agree with the
  emulator by accident.
- **Differential fuzzing** (`tools/fuzz_diff.py`) — generates random straight-line programs,
  runs each through both paths, and compares all registers, the condition code and 4 KiB of
  storage.

```bash
for s in 1 7 11 23 42; do python3 tools/fuzz_diff.py --iterations 40 --seed $s; done
```

  Latest run: **200 programs, 0 mismatches, 46 skipped.** The skips take a program check
  (divide by zero, odd first operand) which the C path is documented not to model, so they are
  counted rather than quietly passing.

The example programs' expected results come from Python's own arithmetic
(`sum([10,20,30,40,50])`, `math.factorial(10)`, `"HELLO WORLD".encode("cp037")`), not from the
emulator, so they are independent checks rather than golden files.

### Bugs this test suite caught

These are worth listing, because they are the argument for building the thing rather than
describing it:

- **`MVC` copied through a snapshot.** Every non-overlapping test passed; the overlap test
  failed. The PoP: "the result is obtained as if each result byte were stored immediately after
  the necessary operand byte is fetched." Byte-at-a-time it is.
- **`BCR` read its mask from a register** instead of its own `M1` field, so `BCR 15,14` did not
  return from a subroutine.
- **`BXLE` compared against R0** instead of the implied comparand register, so a table walk
  looped the wrong number of times.
- **The translated `CVD` used 16 digits** instead of 15, shifting every packed value one nibble.
- **The translated `PACK`/`UNPK` left-aligned** where the PoP right-aligns, and `UNPK` reversed
  the digit order.
- **The interpreter's `UNPK` had a wrong length formula** (`2L+3` instead of `2L+1`), which the
  translator — written independently — disagreed with.
- **The translator's 32-bit shifts were undefined behaviour** for counts ≥ 32: `R[i] <<= n` on a
  `uint32_t` with `n` up to 63. gcc silently produced 0 where the machine produces all sign
  bits. Now guarded explicitly.
- **The assembler treated `SLL 1,32`'s `32` as a register number.** For shifts the second
  operand is the shift count (a displacement), not a register — so no shift test had ever gone
  through the assembler until this was found.
- **`ICM`'s condition code used the first inserted byte with a high bit set**, rather than the
  leftmost inserted byte, which is what the PoP specifies. Both the interpreter and the
  translator had it wrong in the same way — mirroring a bug is still a bug.

The last four were all found by one program, `examples/packed.s370`, which exists purely to
make the interpreter and the translator disagree if either is wrong.

## Examples

| Program | Exercises |
|---|---|
| `sum.s370` | base register, loop with BCT, table of fullwords |
| `factorial.s370` | 64-bit multiply and truncation, 10! = 3628800 |
| `strings.s370` | MVC, TRT, TR with a 256-byte EBCDIC case table |
| `search.s370` | BAL/BCR subroutine call and return, BXLE table walk |
| `packed.s370` | CVD/CVB, PACK/UNPK, ICM/STCM, 64-bit shift, compare-and-swap |
| `shifts.s370` | shift amounts of 32 and above, arithmetic vs logical |
| `icm.s370` | ICM's condition code rule |

## Limits, stated plainly

- **No DAT, no virtual storage, no I/O.** This is a CPU, not a system. Privileged instructions
  raise a program check.
- **The C translation does not model program checks.** It assumes the program stays inside its
  16 MiB store and does not divide by zero. Where the interpreter raises, the C version calls
  `unimplemented` or is undefined. The equivalence claim is therefore over in-range programs,
  and the tests only claim what they check.
- **Translation is static.** Code reached only through a computed branch that the translator
  cannot resolve is reported at run time rather than translated. Base-register values from
  `BALR r,0` are propagated so ordinary `USING`-style code resolves correctly.
- **No decimal arithmetic.** `AP`/`SP`/`MP`/`DP`/`ZAP` and friends are unimplemented; `PACK`,
  `UNPK`, `CVD` and `CVB` are implemented and tested.
- **Not translated:** SVC, MVCL, CLCL, EX, CDS, LPSW, SSM, LRA. The interpreter runs these (or
  raises for the privileged ones); the C translator does not emit code for them, and
  `tests/test_coverage.py` fails if this list and the code disagree.
- **Self-modifying programs are not translated.** The generated C checks every store against
  the translated instruction ranges and stops with a message rather than continuing with stale
  code. The interpreter handles such programs correctly, so this is a real difference between
  the two paths, not a shared limitation.

## Why this exists

The 5100 could not do this. It had 64 KB of address space, no mass storage beyond a 200 KB tape
cartridge, and no way to run a System/370 program. What it had was a microcoded interpreter for
one mainframe APL implementation, which is a genuinely remarkable thing that is not the same
thing as emulating a mainframe.

This repository builds the thing the myth described, so the difference is measurable: one runs
on a laptop and is checked against itself and an external review, and the other never
existed.

The companion documentation dossier is
[luckyseoul/ibm-5100-docs](https://github.com/luckyseoul/ibm-5100-docs) — with the primary
sources, the Titor provenance, and the System/370 analysis.

## Licence

MIT. IBM manuals and the Principles of Operation are © IBM and are linked, not redistributed.
