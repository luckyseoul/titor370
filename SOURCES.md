# Sources

Every opcode and every semantic rule in this repository comes from IBM's *System/370
Principles of Operation*, not from memory and not from another emulator.

## The document

> IBM Corporation, **IBM System/370 Principles of Operation**, GA22-7000-4, Fourth Edition,
> September 1975 (as revised 1 September 1975 by TNL GN22-0498).

Chosen deliberately: September 1975 is contemporary with the IBM 5100's announcement, so it
describes the architecture the 5100's APL implementation came from rather than a later
extension.

Scan: <https://bitsavers.org/pdf/ibm/370/princOps/GA22-7000-4_370_Principles_Of_Operation_Sep75.pdf>
(329 pages, OCR'd by the Internet Archive's Paper Capture service).

The manual is © IBM and is **not redistributed here**. This repository cites it by printed page.

## How the opcode table was derived

Appendix C, *Instructions Arranged by Operation Code*, printed pages 262–267, was processed in
two independent ways:

1. **Two OCR passes.** The page text was extracted and parsed twice with different structural
   assumptions — once keyed on the opcode column, once on the mnemonic column. 117
   opcode/mnemonic pairs agreed between the passes.
2. **Page images.** The 20 pairs that disagreed were resolved by rendering the pages with
   `pdftoppm` at 150 dpi and reading them. Every OCR artefact in the disagreements
   (`LPEB`→`LPER`, `8XH`→`BXH`, `T8`→`TS`, `01`→`OI`, `C5`→`CS`) was resolved this way, not
   guessed.

`tools/pop_opcodes_ocr.json` holds the raw parsed rows so the derivation can be audited.

## How the semantics were derived

Encodings were not enough — several instructions have rules that are easy to get wrong and
that other emulators have got wrong. These were read from the instruction descriptions (text
where reliable, page images where the OCR was poor):

| Instruction | Rule | Page |
|---|---|---|
| `BC` / `BCR` | The R1 field is a four-bit mask M1, not a register number | 121 |
| `BAL` / `BALR` | Link information is the updated IA; R2 = 0 means no branch | 121 |
| `BXH` / `BXLE` | Comparand register is odd and is R3 or R3+1; increment applies either way | 122 |
| `ICM` | Condition code follows the **leftmost inserted byte**, not the first with a high bit | 130 |
| `SLA` | Overflow when a bit unlike the sign shifts out of position 1 | 139–140 |
| shifts | Shift amount is the low six bits of the *second-operand address*; B2 participates | 139 |
| shifts | Amounts 31–63 shift the whole field out: 0 for positive, −1 for negative | 140 |
| `MVC` | Result is as if each byte is stored immediately after the operand byte is fetched | 133 |
| `PACK` | Sign comes from the zone over the low-order digit; field is right-aligned; **L1 and L2 are separate fields** | 137, 146 |
| `UNPK` | Inverse of PACK; sign occupies the zone of the low-order byte | 146 |
| `TRT` | Operand address into R1 (bits 8-31), function byte into R2 (bits 24-31); CC 2 only when the hit is the last byte | 146 |
| `MR` / `M` | The multiplicand is the **odd** register of the pair | 136 |
| `DR` / `D` | Remainder into the even register, quotient into the odd | 128 |
| `MVN` / `MVZ` | MVN moves the low-order (numeric) bits; MVZ the high-order (zone) bits | 135-136 |
| `SL` / `SLR` | Condition code from the carry out of the sign position; CC 0 unreachable | 144 |
| `SLA` / `SLDA` | The sign bit never shifts; only the integer bits do | 139 |
| `TS` | Condition code from the leftmost bit of the old byte | 144 |
| `BAL` / `BALR` | Link word = ILC, CC, program mask, address | 121 |
| `ICM` / `STCM` / `CLM` | Storage operand is **contiguous**, not mask-addressed | 130, 141 |
| `MVCL` / `CLCL` | Even-odd register pairs; 24-bit lengths in R1+1/R2+1; pad in R2+1 bits 0-7; CC 3 = destructive overlap | 126, 133 |
| `CVB` | Out of range loads the low 32 bits and raises a fixed-point **divide** exception | 128 |
| `CS` / `CDS` | Operand must be word- / doubleword-aligned | 123 |
| `EX` | `EX` of `EX` is an execute exception; R1 = 0 means no modification | 129 |

## Correction

An independent review against Hercules (`REVIEW.md`) found that several of these rules had
been read wrongly the first time — `MR`, `DR`, `MVN`/`MVZ`, `SL`/`SLR`, `TRT`, `SLA`, `TS`,
`ICM` and `PACK`'s length fields among them. Each has now been re-derived from the page cited
above, and the tests that encoded the misreadings were rewritten rather than adjusted to pass.

One rule the review stated too broadly is worth recording: `PACK`/`UNPK`/`MVO` and the decimal
operations have two 4-bit length fields, but `MVC`, `TR`, `TRT`, `CLC` and the rest have a
single 8-bit length. Both forms are implemented.

## Known limits of this derivation

- **OCR is not proof.** Where the text layer was legible and unopposed by a second source, it
  was used without rendering the page. A handful of encodings therefore rest on OCR alone,
  though all are cross-checked against the instruction descriptions in `tests/test_isa.py`.
- **The PoP is not the whole machine.** It does not describe timing, the channel subsystem, or
  DAT in the detail an emulator would need — none of which is implemented here.
- **Program checks are modelled in the interpreter but not in the translated C.** Where the
  interpreter raises (addressing, specification, divide), the C program either calls
  `unimplemented` or is undefined. The equivalence tests only claim what they check.

## Secondary sources used for context only

- IBM GA22-7000 does not describe the 5100. For the 5100 side of the story, see the companion
  dossier: <https://github.com/luckyseoul/ibm-5100-docs>, whose primary sources include
  A. J. Cubbon's 1977 account of the 5100 design and the IBM 5100 maintenance manual.
- The "John Titor" claim itself is documented, with provenance, in
  [that dossier's myth page](https://github.com/luckyseoul/ibm-5100-docs/blob/main/docs/system370/myths.md).
