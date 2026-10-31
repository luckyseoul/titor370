# titor370

**The machine John Titor described, built so it can be checked.**

In 2000, the "John Titor" internet persona claimed the IBM 5100 had an undocumented
ability to *"emulate IBM System/370 mainframes and translate between their machine code and
more modern languages."*

The first half of that is false, and [the documentation
says so](https://github.com/luckyseoul/ibm-5100-docs): the 5100 ran IBM's System/370 *APL
implementation* on a microcoded interpreter; it never executed S/370 instructions. The second
half is the interesting part — nobody has ever had software that translates S/370 machine code
into a modern language and *proves* the translation is faithful.

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
CLM STCM ICM`, `TM MVI NI OI XI CLI`, `MVC MVN MVZ NC OC XC CLC TR TRT PACK UNPK`, `TS DIAG`.

Deliberately **not** implemented, and they raise a program check rather than pretending:
`SPM SSK ISK WRD RDD STNSM STOSM SIO TIO HIO TCH`, the decimal arithmetic instructions
(`AP SP MP DP ZAP CP ED EDMK SRP MVO`), `LPSW` and `SSM` (privileged), and DAT.

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
260 passed
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

The example programs' expected results come from Python's own arithmetic
(`sum([10,20,30,40,50])`, `math.factorial(10)`, `"HELLO WORLD".encode("cp037")`), not from the
emulator, so they are independent checks rather than golden files.

### A bug this test suite caught

The first version of `MVC` copied through a snapshot. Every non-overlapping test passed. The
overlap test failed, and reading the PoP showed why: "the result is obtained as if each result
byte were stored immediately after the necessary operand byte is fetched." Byte-at-a-time it
is, then.

The same process caught `BCR` reading its mask from a register instead of its own field, and
`BXLE` comparing against R0 instead of the implied comparand register — both of which made
the subroutine example return to the wrong address.

## Examples

| Program | Exercises |
|---|---|
| `sum.s370` | base register, loop with BCT, table of fullwords |
| `factorial.s370` | 64-bit multiply and truncation, 10! = 3628800 |
| `strings.s370` | MVC, TRT, TR with a 256-byte EBCDIC case table |
| `search.s370` | BAL/BCR subroutine call and return, BXLE table walk |

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

## Why this exists

The 5100 could not do this. It had 64 KB of address space, no mass storage beyond a 200 KB tape
cartridge, and no way to run a System/370 program. What it had was a microcoded interpreter for
one mainframe APL implementation, which is a genuinely remarkable thing that is not the same
thing as emulating a mainframe.

This repository builds the thing the myth described, so the difference is measurable: one runs
on a laptop and proves its own correctness, and the other never existed.

The companion documentation dossier is
[luckyseoul/ibm-5100-docs](https://github.com/luckyseoul/ibm-5100-docs) — with the primary
sources, the Titor provenance, and the System/370 analysis.

## Licence

MIT. IBM manuals and the Principles of Operation are © IBM and are linked, not redistributed.
