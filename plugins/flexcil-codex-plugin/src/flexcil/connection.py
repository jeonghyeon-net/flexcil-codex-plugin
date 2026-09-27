"""Per-user connection setup; APK inspection reads only public OAuth strings."""
from __future__ import annotations

import hashlib
import io
import plistlib
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from .codec import FlexcilError, json_bytes
from .direct import auth_start, private_path
from . import state


def run_receiver_tool(arguments, *, timeout=45):
    try:
        return subprocess.run(arguments, capture_output=True, text=True, check=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError) as exc:
        raise FlexcilError("callback_setup_failed", "Local OAuth callback setup failed; check the macOS Swift runtime and application registration") from exc


def discover_config(profile, apk_path):
    """Read a supplied APK/XAPK without executing or redistributing app code."""
    state.load_profile(profile)
    path = Path(apk_path)
    if path.stat().st_size > 512 * 1024 * 1024:
        raise FlexcilError("limit_exceeded", "APK exceeds 512 MiB")
    raw = path.read_bytes()
    clients, callbacks = set(), set()

    def inspect(data, nested=False):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                members = archive.infolist()
                if len(members) > 100000 or sum(i.file_size for i in members) > 1024 * 1024 * 1024:
                    raise FlexcilError("limit_exceeded", "APK expanded data exceeds limits")
                for entry in members:
                    if re.fullmatch(r"classes[0-9]*\.dex", entry.filename):
                        dex = archive.read(entry)
                        clients.update(re.findall(rb"[0-9]{8,}-[A-Za-z0-9_-]+\.apps\.googleusercontent\.com", dex))
                        callbacks.update(re.findall(rb"com\.flexcil\.[A-Za-z0-9_.]+://", dex))
                    elif not nested and entry.filename.endswith(".apk"):
                        inspect(archive.read(entry), nested=True)
        except zipfile.BadZipFile as exc:
            raise FlexcilError("invalid_apk", "APK must be a valid ZIP package") from exc

    inspect(raw)
    if len(clients) != 1 or len(callbacks) != 1:
        raise FlexcilError("ambiguous_oauth_config", "Expected one client and one callback; provide a reviewed client-config instead",
                           client_count=len(clients), callback_count=len(callbacks))
    config = {"client_id": next(iter(clients)).decode(), "redirect_uri": next(iter(callbacks)).decode(),
              "source_sha256": hashlib.sha256(raw).hexdigest(), "signature_verified": False}
    target = private_path(profile, "client.json")
    state.private_dir(target.parent)
    state.write_file(target, json_bytes(config), replace=True)
    return {"profile": profile, "client_config": str(target), "source_sha256": config["source_sha256"],
            "signature_verified": False, "scope": "Public configuration only; package not executed",
            "notice": "Google consent displays the app identity from the supplied configuration. This plugin will receive and use the new connection."}


def prepare_receiver(profile, config):
    scheme = urlsplit(config["redirect_uri"]).scheme
    if sys.platform != "darwin" or scheme in ("http", "https"):
        return {"status": "external_callback_required", "callback_file": str(private_path(profile, "callback.txt"))}
    compiler = shutil.which("swiftc")
    if not compiler:
        raise FlexcilError("callback_runtime_missing", "macOS automatic callback setup needs Swift command-line tools, or an existing callback handler")
    source = Path(__file__).resolve().parents[2] / "scripts/oauth_receiver.swift"
    folder = state.private_dir(private_path(profile, "receiver"))
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    executable = folder / ("receiver-" + source_hash[:16])
    if not executable.exists():
        run_receiver_tool([compiler, str(source), "-o", str(executable)], timeout=180)
    old = run_receiver_tool([str(executable), "--handler", scheme]).stdout.strip()
    if old != "none" and not old.startswith("net.flexcilcodex.connection"):
        raise FlexcilError("callback_handler_exists", "This callback already belongs to another installed app; do not replace its handler", bundle_id=old)
    bundle_id = "net.flexcilcodex.connection." + hashlib.sha256(str(folder).encode()).hexdigest()[:16]
    bundle = folder / "Flexcil Codex Connection.app"
    binary = bundle / "Contents/MacOS/receiver"
    binary.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(executable, binary)
    values = {"CFBundleIdentifier": bundle_id, "CFBundleName": "Flexcil Codex Connection",
              "CFBundleDisplayName": "Flexcil Codex Connection", "CFBundleExecutable": "receiver",
              "CFBundlePackageType": "APPL", "LSUIElement": True,
              "ConnectionDirectory": str(private_path(profile, "pending.json").parent),
              "CFBundleURLTypes": [{"CFBundleURLName": "Explicit plugin connection", "CFBundleURLSchemes": [scheme]}]}
    state.write_file(bundle / "Contents/Info.plist", plistlib.dumps(values), replace=True)
    run_receiver_tool([str(executable), "--register", str(bundle)])
    run_receiver_tool([str(executable), "--select-handler", scheme, bundle_id])
    return {"status": "local_callback_ready", "bundle_path": str(bundle),
            "callback_file": str(private_path(profile, "callback.txt"))}


def connect(profile, config_path=None):
    config_path = config_path or private_path(profile, "client.json")
    config = state.read_json(config_path)
    # Validate the client before creating a receiver. Start again after compilation
    # so a slow first Swift build does not consume the login's expiry window.
    auth_start(profile, config_path)
    receiver = prepare_receiver(profile, config)
    callback = private_path(profile, "callback.txt")
    callback.unlink(missing_ok=True)  # Only this plugin's previous one-time callback.
    result = auth_start(profile, config_path)
    return {**result, **receiver,
            "next": "Open authorization_url in the system browser. The user completes consent, then connect-finish consumes callback_file."}
