#!/usr/bin/env python3
"""Package only Git-indexed source. Reject private artifacts and local caches."""
import argparse
from pathlib import Path
import subprocess
import zipfile

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    names = [name for name in names if name]
    if not names:
        raise SystemExit("Stage the reviewed public source before packaging")
    forbidden = {".flx", ".flex", ".apk", ".xapk", ".dex", ".pyc", ".list"}
    for name in names:
        path = Path(name)
        if path.suffix in forbidden or any(part in {"work", "profiles", "operations", "__pycache__", ".venv"} or part.startswith(".env") for part in path.parts):
            raise SystemExit("Refusing private/runtime artifact: " + name)
        if (root/path).is_symlink():
            raise SystemExit("Refusing symbolic link: " + name)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "x", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(names):
            archive.write(root/name, "flexcil-codex-plugin/" + name)
    print(args.output.resolve())

if __name__ == "__main__":
    main()
