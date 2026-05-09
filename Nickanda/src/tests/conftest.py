from __future__ import annotations

import sys
from pathlib import Path

# Ensure both ``kvstore.*`` (from src/) and ``tests.*`` (from src/tests/)
# resolve consistently regardless of how pytest discovers tests.
_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
	sys.path.insert(0, str(_SRC))
