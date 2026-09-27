"""Recoverable updates to existing native documents and library files."""
from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone

from . import state
from .codec import Document, FlexcilError, json_bytes
from .direct import Drive
from .edits import apply_edits, identifier
from .library import Library


def native_file(drive, key, folder, sync_id):
    meta = drive.metadata(key)
    props = meta.get("appProperties", {})
    if (meta.get("trashed") or folder not in meta.get("parents", [])
            or props.get("Flexcil") != "MakeFromFlexcil" or props.get("SyncId") != sync_id):
        raise FlexcilError("registration_mismatch", "File does not match the selected native library")
    return meta


def select_file(files, sync_id):
    matches = [item for item in files if item.get("appProperties", {}).get("SyncId") == sync_id]
    if len(matches) != 1:
        raise FlexcilError("ambiguous_file", "Native SyncId must match exactly one Drive file", sync_id=sync_id)
    return matches[0]["id"]


def cloud_tree(profile, drive=None):
    settings = state.load_profile(profile)
    drive = drive or Drive(profile)
    probe = drive.probe()
    files = drive.list(settings["library_folder_id"])
    trash = select_file(files, ".trash.list")
    library = Library(drive.download(probe["library_file_id"]), drive.download(trash))
    return {**library.tree(), "files": [{"file_id": f["id"], "sync_id": f["appProperties"]["SyncId"]} for f in files if "SyncId" in f.get("appProperties", {})]}


def prepare_update(profile, plan, *, file_id=None, probe=False, drive=None):
    settings = state.load_profile(profile)
    if not settings.get("sync_verified") and not probe:
        raise FlexcilError("sync_probe_required", "Verify this library before regular writes")
    drive = drive or Drive(profile)
    capability = drive.probe()
    folder = settings["library_folder_id"]
    pending = []
    if file_id:
        before = drive.download(file_id)
        document = Document.load(before)
        meta = native_file(drive, file_id, folder, document.info["key"])
        candidate, report = apply_edits(document, plan)
        if any(op.get("op") == "rename" for op in plan["operations"]):
            raise FlexcilError("library_rename_required", "Rename through cloud-prepare-library so the document and library title change together")
        pending.append((file_id, document.info["key"], before, candidate.to_bytes(), meta))
    else:
        files = drive.list(folder)
        ids = {"active": capability["library_file_id"], "trash": select_file(files, ".trash.list")}
        before = {kind: drive.download(key) for kind, key in ids.items()}
        library = Library(before["active"], before["trash"])
        candidate, report = library.edit(plan)
        # A document title is stored in both its archive and its library node.
        # Prepare each affected document once, using the last requested title.
        renamed = {}
        for op in plan["operations"]:
            if op["op"] == "rename":
                item = library.find(op["item_id"])[0]
                if item.get("document"):
                    renamed[item["document"]] = op["title"]
        for sync_id, title in renamed.items():
            key = select_file(files, sync_id)
            raw = drive.download(key)
            document = Document.load(raw)
            if document.info["key"] != sync_id:
                raise FlexcilError("wrong_document", "Document identity differs from the library reference")
            updated, _ = apply_edits(document, {"schema": 1, "expected_sha256": document.original_sha256,
                                               "operations": [{"op": "rename", "title": title}]})
            pending.append((key, sync_id, raw, updated.to_bytes(), native_file(drive, key, folder, sync_id)))
        after = candidate.bytes()
        for kind in report["upload_order"]:
            sync_id = "documents.list" if kind == "active" else ".trash.list"
            pending.append((ids[kind], sync_id, before[kind], after[kind], native_file(drive, ids[kind], folder, sync_id)))
    op_id = identifier()
    directory = state.private_dir(state.operation_path(op_id))
    now = time.time()
    rows = []
    for index, (key, sync_id, before, after, meta) in enumerate(pending):
        prefix = str(index)
        props = meta["appProperties"]
        metadata = {"modifiedTime": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                    "appProperties": {**props, "modifiedTime": str(now)}}
        values = {prefix + ".before": before, prefix + ".candidate": after, prefix + ".metadata": json_bytes(metadata)}
        for name, data in values.items():
            state.write_file(directory / name, data)
        rows.append({"file_id": key, "sync_id": sync_id, "prefix": prefix,
                     "hashes": {name: hashlib.sha256(data).hexdigest() for name, data in values.items()}})
    receipt = {"schema": 1, "kind": "cloud_update", "operation_id": op_id, "profile": profile,
               "folder_id": folder, "library_file_id": capability["library_file_id"], "probe": probe,
               "state": "prepared", "created_at": now, "files": rows, "report": report,
               "concurrency": "Fresh byte comparison; multi-file updates are not atomic"}
    state.write_file(directory / "receipt.json", json_bytes(receipt))
    return receipt


def publish_update(operation_id, drive=None):
    receipt = state.receipt(operation_id)
    if receipt.get("kind") != "cloud_update":
        raise FlexcilError("wrong_operation", "Expected a cloud update operation")
    settings = state.load_profile(receipt["profile"])
    if settings["library_folder_id"] != receipt["folder_id"]:
        raise FlexcilError("profile_changed", "The profile now points to another library")
    drive = drive or Drive(receipt["profile"])
    if drive.probe()["library_file_id"] != receipt["library_file_id"]:
        raise FlexcilError("library_changed", "Native library identity changed")
    directory = state.operation_path(operation_id)
    values = {}
    for row in receipt["files"]:
        for name, digest in row["hashes"].items():
            data = (directory / name).read_bytes()
            if hashlib.sha256(data).hexdigest() != digest:
                raise FlexcilError("candidate_changed", "Prepared transaction changed", file=name)
            values[name] = data

    def preflight(row):
        native_file(drive, row["file_id"], receipt["folder_id"], row["sync_id"])
        current = drive.download(row["file_id"])
        prefix = row["prefix"]
        if current not in (values[prefix + ".before"], values[prefix + ".candidate"]):
            state._record(operation_id, "conflict", conflict_file_id=row["file_id"])
            raise FlexcilError("stale_source", "Remote file changed; preserve partial progress and re-plan", file_id=row["file_id"])
        return current == values[prefix + ".candidate"]

    # Check all inputs before any write, then again immediately before each step.
    for row in receipt["files"]:
        preflight(row)
    complete = []
    for row in receipt["files"]:
        prefix = row["prefix"]
        if not preflight(row):
            drive.upload(values[prefix + ".candidate"], state.read_json(directory / (prefix + ".metadata")), row["file_id"],
                         session_path=directory / (prefix + ".upload"))
        if drive.download(row["file_id"]) != values[prefix + ".candidate"]:
            state._record(operation_id, "readback_mismatch", conflict_file_id=row["file_id"])
            raise FlexcilError("readback_mismatch", "Readback differs; inspect before retrying")
        complete.append(row["file_id"])
        state._record(operation_id, "publishing", verified_file_ids=complete)
    return state._record(operation_id, "cloud_verified", verified_file_ids=complete)
