"""Native document construction and a recoverable direct-Drive create transaction."""
from __future__ import annotations

import copy
import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path

from .codec import Document, FlexcilError, encode_list, json_bytes
from .edits import _new_pages, identifier, diff_documents
from .library import Library
from .direct import Drive, MIME
from . import state


def build_document(title, pdf=None, *, width=595, height=842, document_id=None, item_id=None):
    title = Library.title(title)
    key, item = identifier(document_id), identifier(item_id)
    if key == item:
        raise FlexcilError("duplicate_id", "Document UUID and list-item UUID must be distinct")
    now = time.time()
    # A blank background is represented as a PDF document, without pretending to
    # reproduce native cover/template presets that have not been validated.
    info = {"key": key, "version": "0.0.5", "name": title, "type": 0,
            "useCover": False, "createDate": now, "modifiedDate": now, "attachments": {}}
    document = Document({"info": json_bytes(info), "pages.index": b"[]"})
    operation = {"op": "insert_pdf", "pdf": str(pdf), "attachment_id": key} if pdf else {"op": "add_blank_page", "width": width, "height": height, "attachment_id": key}
    pages = []
    _new_pages(document, operation, pages)
    document.entries["pages.index"] = json_bytes(pages)
    info = document.info
    info["currentPage"] = pages[0]["key"]
    document.entries["info"] = json_bytes(info)
    document.entries[".itemInfo"] = encode_list({"originKey": item, "favorite": False})
    document.validate()
    node = {"key": item, "version": "0.0.5", "state": "valid", "name": title,
            "type": info["type"], "document": key, "favorite": False,
            "createDate": now, "modifiedDate": now}
    return Document.load(document.to_bytes()), node


def place_new(library, node, folders):
    if not isinstance(folders, list) or len(folders) > 100:
        raise FlexcilError("invalid_folder_path", "Use up to 100 folder names")
    result = copy.deepcopy(library)
    for existing, _, _ in result._walk(result.active):
        if existing["key"] == node["key"] or existing.get("document") == node["document"]:
            raise FlexcilError("duplicate_id", "New document or list-item ID already exists")
    target, parents, now = result.active, [], time.time()
    for name in folders:
        Library.title(name)
        matches = [n for n in target if n.get("name") == name and n.get("state") != "removed"]
        if len(matches) > 1:
            raise FlexcilError("ambiguous_name", "Folder path matches multiple items", title=name)
        if matches:
            parent = matches[0]
            if parent.get("type") != 1 or parent.get("document"):
                raise FlexcilError("not_folder", "Folder path collides with a document", title=name)
        else:
            parent = {"key": identifier(), "version": "0.0.5", "state": "valid", "name": name,
                      "type": 1, "favorite": False, "color": "Blue", "createDate": now,
                      "modifiedDate": now, "children": []}
            target.append(parent)
        parents.append(parent)
        target = parent.setdefault("children", [])
    if any(n.get("name") == node["name"] and n.get("state") != "removed" for n in target):
        raise FlexcilError("ambiguous_name", "A document with this title already exists in the destination")
    target.append(copy.deepcopy(node))
    for parent in parents:
        parent["modifiedDate"] = now
    result.validate()
    return result


def metadata(document, root, drive_id):
    info = document.info
    iso = lambda seconds: datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return {"id": drive_id, "name": info["key"] + ".flx", "parents": [root], "mimeType": MIME,
            "createdTime": iso(info["createDate"]), "modifiedTime": iso(info["modifiedDate"]),
            "appProperties": {"Flexcil": "MakeFromFlexcil", "SyncId": info["key"], "Type": "Document",
                              "createdTime": str(info["createDate"]), "modifiedTime": str(info["modifiedDate"])}}


def prepare_new(profile, title, pdf=None, folders=None, width=595, height=842, probe=False, drive=None):
    settings = state.load_profile(profile)
    if not settings.get("sync_verified") and not probe:
        raise FlexcilError("sync_probe_required", "Use a dedicated test document for initial connection validation")
    drive = drive or Drive(profile)
    capability = drive.probe()  # Prove private-property visibility before preparing remote writes.
    source = drive.download(capability["library_file_id"])
    library = Library(source)
    document, node = build_document(title, pdf, width=width, height=height)
    updated = place_new(library, node, folders or [])
    candidate = document.to_bytes()
    library_candidate = updated.bytes()["active"]
    key = drive.generate_id()  # Retry always uses this reserved ID, never another POST identity.
    op = identifier()
    directory = state.operation_path(op)
    state.private_dir(directory)
    meta = metadata(document, settings["library_folder_id"], key)
    values = {"document.flx": candidate, "documents.list.before": source, "documents.list": library_candidate,
              "metadata.json": json_bytes(meta)}
    for name, data in values.items():
        state.write_file(directory / name, data)
    receipt = {"schema": 1, "kind": "create_document", "operation_id": op, "profile": profile,
               "folder_id": settings["library_folder_id"], "state": "prepared", "probe": probe,
               "created_at": time.time(), "title": title, "folders": folders or [],
               "document_id": document.info["key"], "item_id": node["key"], "file_id": key,
               "library_file_id": capability["library_file_id"],
               "hashes": {name: hashlib.sha256(data).hexdigest() for name, data in values.items()},
               "device_verified": False}
    state.write_file(directory / "receipt.json", json_bytes(receipt))
    return receipt


def publish_new(operation_id, drive=None):
    receipt = state.receipt(operation_id)
    if receipt.get("kind") != "create_document":
        raise FlexcilError("wrong_operation", "This is not a document creation transaction")
    directory = state.operation_path(operation_id)
    data = {}
    for name, expected in receipt["hashes"].items():
        data[name] = (directory / name).read_bytes()
        if hashlib.sha256(data[name]).hexdigest() != expected:
            raise FlexcilError("candidate_changed", "Creation transaction contents changed", file=name)
    settings = state.load_profile(receipt["profile"])
    if settings["library_folder_id"] != receipt["folder_id"]:
        raise FlexcilError("profile_changed", "Profile now refers to a different library")
    drive = drive or Drive(receipt["profile"])
    capability = drive.probe()
    if capability["library_file_id"] != receipt["library_file_id"]:
        raise FlexcilError("library_changed", "Native documents.list file identity changed")
    current_list = drive.download(receipt["library_file_id"])
    if current_list not in (data["documents.list.before"], data["documents.list"]):
        raise FlexcilError("stale_source", "Library changed since preparation; preserve this transaction and re-plan")
    try:
        existing = drive.metadata(receipt["file_id"])
    except FlexcilError as exc:
        if exc.code != "google_http_error" or exc.details.get("status") != 404:
            raise
        existing = None
    if existing is None:
        if current_list == data["documents.list"]:
            raise FlexcilError("missing_document", "Published library refers to a missing document; do not recreate it blindly")
        drive.upload(data["document.flx"], state.read_json(directory / "metadata.json"), session_path=directory / "document-upload.json")
        existing = drive.metadata(receipt["file_id"])
    props = existing.get("appProperties", {})
    if existing.get("trashed") or props.get("Flexcil") != "MakeFromFlexcil" or props.get("SyncId") != receipt["document_id"] or props.get("Type") != "Document":
        raise FlexcilError("registration_mismatch", "New Drive file does not have the expected native registration")
    if drive.download(receipt["file_id"]) != data["document.flx"]:
        raise FlexcilError("document_changed", "Uploaded document differs; preserve remote content and inspect")
    state._record(operation_id, "document_verified")
    # Re-read after the potentially long document upload, before publishing its reference.
    current_list = drive.download(receipt["library_file_id"])
    if current_list != data["documents.list"]:
        if current_list != data["documents.list.before"]:
            raise FlexcilError("stale_source", "Library changed while uploading; new document retained for recovery")
        meta = drive.metadata(receipt["library_file_id"])
        props = meta.get("appProperties", {})
        if props.get("Flexcil") != "MakeFromFlexcil" or props.get("SyncId") != "documents.list":
            raise FlexcilError("registration_mismatch", "Library metadata changed")
        now = time.time()
        update = {"modifiedTime": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                  "appProperties": {**props, "modifiedTime": str(now)}}
        # Keep the original metadata for an interrupted resumable upload.
        plan_path = directory / "library-metadata.json"
        if plan_path.exists():
            update = state.read_json(plan_path)
        else:
            state.write_file(plan_path, json_bytes(update))
        drive.upload(data["documents.list"], update, receipt["library_file_id"], session_path=directory / "library-upload.json")
    if drive.download(receipt["library_file_id"]) != data["documents.list"]:
        raise FlexcilError("readback_mismatch", "Library readback differs; inspect before any retry")
    return state._record(operation_id, "cloud_verified", verification="Native registration and both raw files verified; tablet display pending")


def verify_new(operation_id, *, device_confirmed=False, drive=None):
    receipt = state.receipt(operation_id)
    if receipt.get("kind") != "create_document":
        raise FlexcilError("wrong_operation", "This is not a document creation transaction")
    if receipt["state"] not in ("cloud_verified", "device_verified"):
        raise FlexcilError("not_published", "Publish and verify registration before checking the device")
    if not device_confirmed:
        raise FlexcilError("device_confirmation_required", "Obtain the user's display and new-mark confirmation first")
    settings = state.load_profile(receipt["profile"])
    if settings["library_folder_id"] != receipt["folder_id"]:
        raise FlexcilError("profile_changed", "The library changed since this operation")
    drive = drive or Drive(receipt["profile"])
    capability = drive.probe()
    if capability["library_file_id"] != receipt["library_file_id"]:
        raise FlexcilError("library_changed", "Native library identity changed")
    directory = state.operation_path(operation_id)
    original = (directory / "document.flx").read_bytes()
    if hashlib.sha256(original).hexdigest() != receipt["hashes"]["document.flx"]:
        raise FlexcilError("candidate_changed", "Prepared document changed")
    remote_data = drive.download(receipt["file_id"])
    before, remote = Document.load(original), Document.load(remote_data)
    diff = diff_documents(before, remote)
    if (not diff["same_document"] or diff["pages_before"] != diff["pages_after"]
            or any(row["removed"] or row["changed"] for row in diff["objects"].values())
            or any(remote.entries.get(k) != v for k, v in before.entries.items() if k.startswith("attachment/"))):
        raise FlexcilError("device_content_changed", "Device changed existing content; inspect the diff", diff=diff)
    if not any(row["added"] for row in diff["objects"].values()):
        raise FlexcilError("device_mark_missing", "No new device object has synced yet")
    library = Library(drive.download(receipt["library_file_id"]))
    matches = [node for node, _, _ in library._walk(library.active) if node.get("document") == receipt["document_id"] and node.get("state") != "removed"]
    if len(matches) != 1 or matches[0]["key"] != receipt["item_id"]:
        raise FlexcilError("library_reference_changed", "Expected one matching native library item")
    state.write_file(directory / "device-readback.flx", remote_data, replace=True)
    settings.update(sync_verified=True, new_document_verified=True)
    state.write_file(state.profile_path(receipt["profile"]), json_bytes(settings), replace=True)
    return state._record(operation_id, "device_verified", device_verified=True,
                         device_readback_sha256=remote.original_sha256, device_diff=diff,
                         verification="User confirmed tablet display; new device objects recovered and original content preserved")
