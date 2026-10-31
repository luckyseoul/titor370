"""titor370 -- the machine John Titor described, built to be checked.

The 5100 never had a System/370 emulator. This is one: a real S/370 interpreter,
a disassembler, an assembler, and a translator that turns S/370 machine code
into C and proves the two agree.
"""

from .asm import assemble
from .cpu import CPU, MEMSIZE, ProgramCheck, S370Error
from .isa import OPCODES, Instruction, decode, encode
from .translate import translate

__all__ = [
    "CPU",
    "MEMSIZE",
    "OPCODES",
    "Instruction",
    "ProgramCheck",
    "S370Error",
    "assemble",
    "decode",
    "encode",
    "translate",
]
__version__ = "0.1.0"
