"""Private profiles and resumable write receipts, outside the installed plugin."""
from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile
import time
from pathlib import Path

from .codec import Document, FlexcilError, json_bytes, parse_json
from .edits import apply_edits, diff_documents, identifier


def state_root():
    override = os.environ.get("FLEXCIL_STATE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    return base / "flexcil-codex"


def private_dir(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise FlexcilError("unsafe_path", "Private state directory must not be a symbolic link")
    if os.name != "nt":
        path.chmod(0o700)
    return path


def write_file(path, data, *, replace=False):
    """Publish a completely written file; new outputs never clobber existing paths."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".flexcil-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            if path.is_symlink():
                raise FlexcilError("unsafe_path", "Refusing to replace a symbolic link")
            os.replace(temp, path)
        else:
            try:
                os.link(temp, path)
            except FileExistsError as exc:
                raise FlexcilError("output_exists", "Output already exists; choose another path", path=str(path)) from exc
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def read_json(path):
    return parse_json(Path(path).read_bytes(), Path(path).name)


def profile_path(name):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
        raise FlexcilError("invalid_profile", "Profile name must contain 1–64 letters, digits, underscores or hyphens")
    return state_root() / "profiles" / (name + ".json")


def configure(name, folder_id, platform="unknown"):
    if not re.fullmatch(r"[A-Za-z0-9_-]{5,200}", folder_id):
        raise FlexcilError("invalid_folder", "Use the verified raw Google Drive folder ID")
    if platform not in ("android", "ios", "unknown"):
        raise FlexcilError("invalid_platform", "platform must be android, ios or unknown")
    path = profile_path(name)
    private_dir(state_root())
    private_dir(path.parent)
    value = {"schema": 1, "profile": name, "library_folder_id": folder_id, "platform": platform, "sync_verified": False}
    if path.exists():
        old = read_json(path)
        if all(old.get(k) == value[k] for k in ("library_folder_id", "platform")):
            return old
    write_file(path, json_bytes(value), replace=True)
    return value


def load_profile(name):
    path = profile_path(name)
    if not path.exists():
        raise FlexcilError("setup_required", "Run setup for this library first", profile=name)
    return read_json(path)


def operation_path(operation_id):
    return state_root() / "operations" / identifier(operation_id)


def prepare(source_path, plan, profile, file_id, operation_id=None, *, probe=False):
    settings = load_profile(profile)
    if not settings.get("sync_verified") and not probe:
        raise FlexcilError("sync_probe_required", "Verify a dedicated test note before writing regular documents")
    if not re.fullmatch(r"[A-Za-z0-9_-]{5,200}", file_id):
        raise FlexcilError("invalid_file_id", "Use the verified raw Drive file ID")
    op_id = identifier(operation_id)
    directory = operation_path(op_id)
    if directory.exists():
        raise FlexcilError("operation_exists", "Resume the existing operation instead of recreating it", operation_id=op_id)
    source_data = Path(source_path).read_bytes()
    source = Document.load(source_data)
    candidate, report = apply_edits(source, plan)
    candidate_data = candidate.to_bytes()
    Document.load(candidate_data)
    private_dir(directory.parent)
    directory.mkdir(mode=0o700)
    write_file(directory / "source.flx", source_data)
    write_file(directory / "candidate.flx", candidate_data)
    write_file(directory / "plan.json", json_bytes(plan))
    manifest = {"schema": 1, "operation_id": op_id, "profile": profile, "file_id": file_id, "folder_id": settings["library_folder_id"], "probe": probe, "created_at": time.time(), "state": "prepared", "candidate_sha256": hashlib.sha256(candidate_data).hexdigest(), **report}
    write_file(directory / "receipt.json", json_bytes(manifest))
    return {**manifest, "source_path": str(directory / "source.flx"), "candidate_path": str(directory / "candidate.flx")}


def receipt(operation_id):
    path = operation_path(operation_id) / "receipt.json"
    if not path.exists():
        raise FlexcilError("operation_not_found", "No complete operation receipt found")
    return read_json(path)


def _record(operation_id, state, **fields):
    current = receipt(operation_id)
    current.update(state=state, updated_at=time.time(), **fields)
    write_file(operation_path(operation_id) / "receipt.json", json_bytes(current), replace=True)
    return current


def preflight(operation_id, remote_path):
    value = receipt(operation_id)
    directory = operation_path(operation_id)
    candidate_hash = hashlib.sha256((directory / "candidate.flx").read_bytes()).hexdigest()
    if candidate_hash != value["candidate_sha256"]:
        raise FlexcilError("candidate_changed", "Prepared candidate was modified after validation")
    current = Document.load(remote_path)
    if current.info["key"] != value["document_id"]:
        raise FlexcilError("wrong_document", "Preflight file belongs to a different document")
    if current.original_sha256 == value["candidate_sha256"]:
        return _record(operation_id, "verified", already_uploaded=True)
    if current.original_sha256 != value["source_sha256"]:
        _record(operation_id, "conflict")
        raise FlexcilError("remote_changed", "Remote changed; download again and re-plan. Do not overwrite it.")
    return _record(operation_id, "ready", preflight_at=time.time(), candidate_path=str(directory / "candidate.flx"), concurrency="byte preflight; connector has no atomic If-Match support")


def verify(operation_id, remote_path, *, device_confirmed=False):
    value = receipt(operation_id)
    remote = Document.load(remote_path)
    directory = operation_path(operation_id)
    if hashlib.sha256((directory / "candidate.flx").read_bytes()).hexdigest() != value["candidate_sha256"]:
        raise FlexcilError("candidate_changed", "Prepared candidate was modified after validation")
    if remote.info["key"] != value["document_id"]:
        raise FlexcilError("wrong_document", "Readback belongs to a different document")
    if not device_confirmed:
        if remote.original_sha256 != value["candidate_sha256"]:
            _record(operation_id, "readback_mismatch")
            raise FlexcilError("readback_mismatch", "Readback differs. Retain both copies; do not retry the upload blindly.")
        return _record(operation_id, "verified", readback_sha256=remote.original_sha256)
    candidate = Document.load(directory / "candidate.flx")
    difference = diff_documents(candidate, remote)
    if difference["pages_after"] != difference["pages_before"] or any(row["removed"] or row["changed"] for row in difference["objects"].values()):
        raise FlexcilError("device_content_changed", "Device copy changed expected pages or objects; inspect the diff", diff=difference)
    # PDF assets must survive as well, even if the device rewrites JSON metadata.
    if any(remote.entries.get(k) != v for k, v in candidate.entries.items() if k.startswith("attachment/")):
        raise FlexcilError("device_content_changed", "An attachment changed during round trip")
    if value.get("probe"):
        profile = load_profile(value["profile"])
        if profile["library_folder_id"] != value["folder_id"]:
            raise FlexcilError("profile_changed", "The library changed since this probe was prepared")
        if not any(row["added"] for row in difference["objects"].values()):
            raise FlexcilError("roundtrip_required", "Probe needs a new tablet object after the edit, plus explicit user confirmation")
        profile.update(sync_verified=True, verified_at=time.time(), verification_operation=operation_id)
        write_file(profile_path(value["profile"]), json_bytes(profile), replace=True)
    return _record(operation_id, "device_verified", readback_sha256=remote.original_sha256, device_diff=difference)


def rollback_candidate(operation_id, remote_path, output_path):
    value = receipt(operation_id)
    current = Document.load(remote_path)
    if current.original_sha256 != value["candidate_sha256"]:
        raise FlexcilError("remote_changed", "Rollback would overwrite later changes; restore through a reviewed new edit")
    original = Document.load(operation_path(operation_id) / "source.flx")
    info = original.info
    info["modifiedDate"] = max(time.time(), current.info.get("modifiedDate", 0) + .001)
    original.entries["info"] = json_bytes(info)
    data = original.to_bytes()
    write_file(output_path, data)
    return {"file_id": value["file_id"], "rollback_path": str(Path(output_path).resolve()), "sha256": hashlib.sha256(data).hexdigest(), "expected_remote_sha256": current.original_sha256, "requires_fresh_preflight": True}
