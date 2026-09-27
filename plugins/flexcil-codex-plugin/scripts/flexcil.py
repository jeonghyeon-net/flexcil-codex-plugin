#!/usr/bin/env python3
"""Run with the Python runtime selected during plugin setup."""
import sys
from pathlib import Path

sys.dont_write_bytecode = True
if sys.version_info < (3, 10):
    raise SystemExit("Flexcil requires Python 3.10+. Use the runtime selected by flexcil-setup.")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from flexcil.cli import main

raise SystemExit(main())
