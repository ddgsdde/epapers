"""Align SDK6's generated initial stack pointer to the ARM ABI's eight bytes."""
import sys
from pathlib import Path

path = Path(sys.argv[1])
source = path.read_text()
before = "__StackTop = ORIGIN(LR_IROM3) + LENGTH(LR_IROM3);"
if before not in source:
    raise RuntimeError("SDK6 linker stack expression changed")
path.write_text(source.replace(before, "__StackTop = (ORIGIN(LR_IROM3) + LENGTH(LR_IROM3)) & ~7;"))
