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
