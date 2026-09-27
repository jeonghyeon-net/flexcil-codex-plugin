#!/usr/bin/env python3
"""Create an isolated Python environment in a caller-selected private directory."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import venv


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True, type=Path)
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        raise SystemExit("Run bootstrap.py with Python 3.10+ (the Codex bundled runtime is supported).")
    root = Path(__file__).resolve().parents[1]
    target = args.directory.expanduser().resolve()
    if target.exists():
        raise SystemExit("Choose a new environment directory; existing environments are never replaced.")
    venv.EnvBuilder(with_pip=True).create(target)
    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(root / "requirements.txt")], check=True)
    subprocess.run([str(python), str(root / "scripts/flexcil.py"), "doctor"], check=True)
    print(json.dumps({"python": str(python), "entrypoint": str(root / "scripts/flexcil.py")}))


if __name__ == "__main__":
    main()
