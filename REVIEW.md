# Independent review: titor370 vs. a real System/370

Method: the same assembled bytes were run on titor370's interpreter, its C translation,
and Hercules (SDL Hyperion, `ARCHLVL S/370`) as an independent oracle. Each rule below
was also checked against Hercules' source. titor370 `07ed7a6`, Hercules `b9dfc2b`.

## Headline results
- `examples/factorial.s370` computes **0** on S/370 (MR multiplies R1+1, which is 0).
- `examples/packed.s370` takes a specification exception at `CS` (TARGET at X'105E' is not
  word-aligned); PACK, UNPK, STCM and CS results differ.
- `examples/strings.s370`: TRT registers swapped, CC 2 instead of 1.
- README fuzz corpus (200 programs): 154 compared, 154 end differently on S/370; 100 still
  differ after correcting only the BALR link word. All 100 contain an instruction below.

## Interpreter (cpu.py) and mirrored code in translate.py
| Instruction | Correct S/370 rule |
|---|---|
| `M`/`MR` | Multiplicand is R1+1 (odd), not R1 |
| `D`/`DR` | Remainder -> R1 (even), quotient -> R1+1 (odd) |
| `MVN`/`MVZ` | Swapped: MVN moves low nibbles, MVZ high nibbles |
| `PACK`/`UNPK` | Length byte is two 4-bit fields L1, L2 (also MVO and decimal ops) |
| `ICM`/`STCM`/`CLM` | Storage operand is contiguous; ICM leaves unselected bytes alone; zero mask: register unchanged (ICM), CC 0 not an exception (CLM) |
| `SL`/`SLR` | CC 1 nonzero no carry, 2 zero carry, 3 nonzero carry (carry = no borrow; CC 0 impossible) |
| `TRT` | Address -> R1 bits 8-31, function byte -> R2 bits 24-31, other bits kept; CC 2 only on the last byte, else 1; registers untouched on CC 0 |
| `BAL`/`BALR` | Link word = ILC, CC, program mask in bits 0-7, then address |
| `MVCL`/`CLCL` | Lengths in bits 8-31 of R1+1/R2+1, pad in bits 0-7 of R2+1, even registers; CC 3 = destructive overlap; CLCL pads the shorter operand |
| `LNR` | -abs(x), CC 0/1 (currently behaves as LCR) |
| `SLA`/`SLDA` | Sign bit is never shifted |
| `TS` | CC = leftmost bit of the old byte |
| `CVB` | Overflow is fixed-point **divide** (code 9), low 32 bits loaded |
| `CS`/`CDS` | Word/doubleword alignment required (specification exception) |
| `BCT`/`BCTR` | Branch address computed before the decrement |
| `EX` | EX of EX is an execute exception; don't patch storage to execute |
| `LRA` | Privileged |
| Unimplemented decimal ops | Should raise operation (1), not privileged-operation (2) |
| Floating point | Absent; list it under "not implemented" |

Tests that currently encode these misreadings: `test_mr_*`, `test_dr_*`, `test_slr_*`,
`test_sla_overflow_sets_cc3`, `test_trt_*`, `test_mvn_and_mvz_*`, `test_pack_*`,
`test_unpack_*` (and the vacuous `test_unpack_is_the_inverse`), `test_icm_inserts_selected_bytes`,
`test_stcm_*`, `test_balr_*`, and the SLR/MR/DR checks in `test_oracle.py`.
Fix `factorial.s370` with `LR 5,1` before `MR 4,2`.

## Translator structure (translate.py)
- Base registers learned globally from `BALR r,0` are never invalidated. Two routines that
  each establish R12 (standard linkage): 96/96 layouts fail (68 compile errors, 28 runtime
  bail-outs). Fix: guard each base-resolved branch with a runtime check of the register,
  falling back to dispatch, and add candidates for every known base value.
- `s370_run` starts at the lowest discovered address, not the entry point: emit `goto L_entry;`.
- Indexed `BC` emits `goto dispatch` without a dispatch label (compile error); branch-table
  entries are never discovered. Always emit dispatch; scan consecutive `B` entries.
- Stores into code are ignored: a NOP switch flipped with `OI` gives R1=2 instead of 1.
  Trap stores into translated instruction bytes.
- Interpreter/translator disagree: `SLA` of 0 by >=32 (CC 0 vs 3), `CLM` zero mask, non-zero `DIAG`.

## Assembler (asm.py)
- SI operands are reversed from IBM syntax (`MVI D1(B1),I2`).
- `FLAG+1` under USING silently becomes base 0, displacement X'013'. Resolve expressions
  through USING; error on displacement > X'FFF'.
- `DC`/`DS` F, H, A, D are not aligned.
- PACK/UNPK lengths are forced equal and packed into one byte.
- `BHE` (mask 3) is not an IBM mnemonic and means "high or overflow".
- `dc`/`_one_constant` are defined twice.

## Verification
Add Hercules in S/370 mode as a CI oracle for every example and the fuzz corpus. The
interpreter/translator diff cannot catch a shared misreading of the PoP. Note: Hercules
itself mis-handles `SLA` with shift amounts >= 32 (undefined 32-bit shift in `general2.c`);
judge that case against the PoP.

## README wording
- "Proves the translation is faithful" -> "verified against its interpreter and Hercules".
- "It never executed S/370 instructions" conflicts with the dossier's own source (Cubbon 1977:
  "the lack of a 370 emulator"; ROM holds "System/370-like instructions") and with 2025 ROM
  analysis finding the emulator's opcode jump tables. Defensible: the 5100 emulated the
  instruction subset the APL interpreter needed, not a 370 system.
- SOURCES.md's TRT row (function byte in R1, address in R2) is reversed.

## What is already right
Base/index-zero rule, BCR mask, BXH/BXLE comparand and increment, MVC overlap, shift counts
(logical and SRA), CVD, ICM's CC rule, AR/SR/ALR CC, compares, STM/LM wrap. `sum`, `icm`,
`shifts` and `search` compute correct answers on S/370 (only the link word differs).

---

# Response

Applied against GA22-7000-4 (September 1975), page by page. Every finding below was
re-checked in the manual before being accepted; two were corrected in the process.

## Fixed, with the page that settles it

| Finding | Verdict | Page | Change |
|---|---|---|---|
| `M`/`MR` multiplicand is R1+1 | **confirmed** | 136 | "The multiplicand is taken from the odd register of the pair" |
| `D`/`DR` remainder → even, quotient → odd | **confirmed** | 128 | "remainder and quotient replace the dividend in the even-numbered and odd-numbered registers, respectively" |
| `MVN`/`MVZ` swapped | **confirmed** | 136 | MVZ moves "the high-order four bits ... (the zones)" |
| `ICM`/`STCM`/`CLM` contiguous operand | **confirmed** | 130, 141 | "fetched from contiguous storage locations"; STCM stores "in successive and contiguous storage locations" |
| `ICM` leaves unselected bytes alone; zero mask is not an error | **confirmed** | 130 | "bytes ... corresponding to zeros in the mask remain unchanged" |
| `SL`/`SLR` condition code | **confirmed** | 144 | CC 0 is printed as "0 -": unreachable. Carry out of the sign position |
| `TRT` registers reversed | **confirmed** | 146 | address → R1 (bits 8-31), function byte → R2 (bits 24-31); CC 2 only on the last byte |
| `LNR` is −abs(x) | **confirmed** | 132 | "complements positive numbers; negative numbers remain unchanged" |
| `SLA`/`SLDA` never shift the sign | **confirmed** | 139 | "The sign of the first operand remains unchanged. All 31 integer bits ... participate" |
| `TS` CC from the leftmost bit | **confirmed** | 144 | "The leftmost bit ... is used to set the condition code" |
| `BAL`/`BALR` link word | **confirmed** | 121 | ILC, CC, program mask, then the address |
| `MVCL`/`CLCL` register pairs and 24-bit lengths | **confirmed** | 133, 126 | lengths in bits 8-31 of R1+1/R2+1; pad in bits 0-7 of R2+1 |
| `CVB` overflow is a fixed-point **divide** exception | **confirmed** | 128 | "a fixed-point divide exception exists"; the low 32 bits are loaded |
| `CS`/`CDS` alignment | **confirmed** | 123 | "must be designated on a word boundary" |
| `BCT`/`BCTR` address before decrement | **confirmed** | 122 | "The branch address is computed before the counting operation" |
| `EX` of `EX` is an execute exception; R1=0 means no modification | **confirmed** | 129 | both stated explicitly |
| `LRA` is privileged | **confirmed** | 132 | listed with the privileged instructions |
| decimal ops raise operation, not privileged-operation | **confirmed** | — | the feature is simply absent |
| `PACK`/`UNPK` take two 4-bit lengths | **confirmed** | 146 | the SS diagram shows L1 and L2 as separate fields |
| Assembler: SI operand order | **fixed** | 129 et al. | `MVI D1(B1),I2` |
| Assembler: constant alignment | **fixed** | — | F/A on fullwords, H on halfwords, D on doublewords; labels name the aligned address |
| Assembler: `BHE` is not an IBM mnemonic | **fixed** | — | removed; `BZ`/`BNZ`/`BM`/`BNM`/`BP`/`BNP` added |
| Assembler: duplicate `dc`/`_one_constant` | **fixed** | — | the dead first pair deleted |
| Translator: start at the entry point | **fixed** | — | `goto L_entry;` |
| Translator: dispatch always emitted | **fixed** | — | fixes compile errors on indexed branches |
| Translator: base registers learned globally | **fixed** | — | each base-resolved branch is guarded by a runtime check of the register, with candidates discovered for every known base value |
| Translator: stores into code ignored | **fixed** | — | `store_guard()` traps them and says why |

## One finding corrected

The review states that `PACK`/`UNPK` "length byte is two 4-bit fields L1, L2 (also MVO and
decimal ops)". That is true only for those instructions. `MVC`, `MVN`, `MVZ`, `NC`, `OC`,
`XC`, `CLC`, `TR` and `TRT` carry a **single 8-bit length** in bits 8-15 — the TR diagram on
p. 145 shows one `L` field, while UNPK on p. 146 shows `L1 | L2`. Both variants are now
implemented, selected per mnemonic.

The review's claim that interpreter and translator disagree on `SLA` of 0 by ≥32 was also not
reproducible: both returned CC 0. The rewrite made that path explicit anyway.

## Not adopted, and why

**"It never executed S/370 instructions" conflicts with the sources.** Partly fair. The
defensible statement is narrower: the 5100 did not emulate a System/370 *machine* — no
instruction set as an architectural commitment, no supervisor, no channels, no virtual
storage — but its microcode did interpret an intermediate instruction stream that Cubbon
describes as "System/370-like". The 5100's documentation now says exactly that rather than
the stronger claim.

**Hercules as a CI oracle.** Not adopted, for two reasons. It would make CI depend on building
Hercules and on ROM images for a 370 system; and the review itself notes that Hercules
mis-handles `SLA` with shift amounts ≥ 32, so it cannot be treated as ground truth for the
rules in dispute. The PoP remains the oracle, and every rule now cites its page.

## Verification after the changes

- 620 tests pass, including new cases for each rule above.
- All seven example programs still agree between interpreter and translated C.
- Differential fuzzing: 200 programs across five seeds, 0 mismatches. Programs that take a
  program check, or that store into their own code, are counted as skips and reported
  separately — the C path does not model either.
