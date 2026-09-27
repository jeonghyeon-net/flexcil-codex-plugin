#!/usr/bin/env python3
"""Run repository checks locally without using the user's Flexcil state."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlsplit
import zipfile


def main():
    root = Path(__file__).resolve().parents[1]
    plugin = root / "plugins/flexcil-codex-plugin"
    manifest = json.loads((plugin / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    engine = re.search(r'__version__\s*=\s*"([^"]+)"', (plugin / "src/flexcil/__init__.py").read_text(encoding="utf-8")).group(1)
    project = re.search(r'^version\s*=\s*"([^"]+)"', (root / "pyproject.toml").read_text(encoding="utf-8"), re.M).group(1)
    if manifest["name"] != plugin.name or manifest["version"].split("+", 1)[0] != engine or engine != project:
        raise SystemExit("Plugin name or base versions do not agree")
    print(f"Version: {engine}", flush=True)

    names = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "*.md"], cwd=root
    ).decode().split("\0")
    checked = 0
    for name in sorted(set(filter(None, names))):
        source = root / name
        if not source.is_file():
            continue
        text = re.sub(r"^```[^\n]*\n.*?^```[^\n]*$", "", source.read_text(encoding="utf-8"), flags=re.M | re.S)
        for target in re.findall(r"\[[^\]\n]*\]\(([^)\n]+)\)", text):
            url = urlsplit(target.strip("<>"))
            if url.scheme or url.netloc or not url.path:
                continue
            path = (source.parent / unquote(url.path)).resolve()
            if not path.is_relative_to(root) or not path.exists():
                raise SystemExit(f"Broken or nonportable file link: {name}: {target}")
            checked += 1
    print(f"Relative file links: {checked}", flush=True)

    with tempfile.TemporaryDirectory(prefix="flexcil-check-") as tmp:
        env = {**os.environ, "PYTHONPATH": str(plugin / "src"), "PYTHONDONTWRITEBYTECODE": "1",
               "PYTHONUTF8": "1", "FLEXCIL_STATE_DIR": str(Path(tmp) / "state")}
        subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"], cwd=root, env=env, check=True)
        doctor = json.loads(subprocess.check_output(
            [sys.executable, str(plugin / "scripts/flexcil.py"), "doctor"], cwd=root, env=env
        ))
        if not doctor.get("ok") or doctor["result"]["version"] != engine:
            raise SystemExit("Packaged CLI runtime check failed")
        print("CLI runtime: passed", flush=True)

        archive_path = Path(tmp) / "source.zip"
        subprocess.run([sys.executable, str(root / "tools/package.py"), str(archive_path)], cwd=root, check=True, stdout=subprocess.DEVNULL)
        checksum = archive_path.with_suffix(".zip.sha256").read_text(encoding="utf-8").split()[0]
        if hashlib.sha256(archive_path.read_bytes()).hexdigest() != checksum:
            raise SystemExit("Package checksum mismatch")
        with zipfile.ZipFile(archive_path) as archive:
            if archive.testzip() is not None:
                raise SystemExit("Package ZIP integrity check failed")
            prefix = "flexcil-codex-plugin/"
            required = ["README.md", ".agents/plugins/marketplace.json", "plugins/flexcil-codex-plugin/.codex-plugin/plugin.json"]
            if any(prefix + name not in archive.namelist() for name in required):
                raise SystemExit("Package is missing a required installation file")
            print(f"Public source package: {len(archive.namelist())} files, checksum verified", flush=True)
    print("Local checks passed. Cloud and tablet tests are separate.", flush=True)


if __name__ == "__main__":
    main()
