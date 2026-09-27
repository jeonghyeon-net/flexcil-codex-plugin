"""Bounded, loss-preserving readers for Flexcil archives and compressed lists."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import stat
import warnings
import struct
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any


class FlexcilError(Exception):
    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code, self.details = code, details


@dataclass(frozen=True)
class Limits:
    archive_bytes: int = 512 * 1024 * 1024
    expanded_bytes: int = 1024 * 1024 * 1024
    entry_bytes: int = 256 * 1024 * 1024
    entries: int = 100_000
    json_bytes: int = 64 * 1024 * 1024
    points_per_stroke: int = 1_000_000


LIMITS = Limits()
LAYERS = ("drawings", "texts", "images", "shapes", "hyperlinks", "maskings")


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def parse_json(data: bytes, label: str = "JSON") -> Any:
    if len(data) > LIMITS.json_bytes:
        raise FlexcilError("limit_exceeded", f"{label} exceeds the JSON size limit")
    def reject(value: str):
        raise ValueError(f"Non-finite JSON number: {value}")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    try:
        return json.loads(data, parse_constant=reject, object_pairs_hook=pairs)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise FlexcilError("invalid_json", f"Invalid {label}: {exc}") from exc


def finite_number(value: Any, name: str, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise FlexcilError("invalid_value", f"{name} must be a finite number")
    if minimum is not None and value < minimum:
        raise FlexcilError("invalid_value", f"{name} must be at least {minimum}")
    return float(value)


def safe_name(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts and "\\" not in name and "\x00" not in name and ":" not in name


def decode_points(encoded: str) -> list[tuple[float, float, float]]:
    if not isinstance(encoded, str) or len(encoded) > (LIMITS.points_per_stroke * 12 + 4) * 2:
        raise FlexcilError("invalid_points", "Invalid or oversized stroke points")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise FlexcilError("invalid_points", "Stroke points are not valid Base64") from exc
    if len(data) < 4:
        raise FlexcilError("invalid_points", "Stroke count header is missing")
    count = struct.unpack_from("<I", data)[0]
    if count > LIMITS.points_per_stroke or len(data) != 4 + count * 12:
        raise FlexcilError("invalid_points", "Stroke count and byte length disagree")
    points = list(struct.iter_unpack("<fff", data[4:]))
    if any(not all(math.isfinite(v) for v in p) for p in points):
        raise FlexcilError("invalid_points", "Stroke contains non-finite coordinates")
    return points


def decode_list(data: bytes) -> Any:
    if len(data) < 8:
        raise FlexcilError("invalid_list", "Compressed list is missing its length header")
    length = struct.unpack_from("<Q", data)[0]
    if length > LIMITS.json_bytes:
        raise FlexcilError("limit_exceeded", "Compressed list expands beyond the limit")
    try:
        decoder = zlib.decompressobj(-15)
        plain = decoder.decompress(data[8:], length + 1)
        if len(plain) != length or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError("Declared length or compressed stream is invalid")
    except (ValueError, zlib.error) as exc:
        raise FlexcilError("invalid_list", str(exc)) from exc
    return parse_json(plain, "compressed list")


def encode_list(value: Any) -> bytes:
    plain = json_bytes(value)
    stream = zlib.compressobj(wbits=-15)
    return struct.pack("<Q", len(plain)) + stream.compress(plain) + stream.flush()


@dataclass
class Document:
    entries: dict[str, bytes]
    original_sha256: str = ""
    zip_info: dict[str, zipfile.ZipInfo] = field(default_factory=dict, repr=False)
    zip_comment: bytes = b""
    duplicate_metadata: list[tuple[zipfile.ZipInfo, bytes]] = field(default_factory=list, repr=False)

    @classmethod
    def load(cls, source: str | Path | bytes) -> Document:
        if isinstance(source, (str, Path)):
            path = Path(source)
            if path.stat().st_size > LIMITS.archive_bytes:
                raise FlexcilError("limit_exceeded", "Archive exceeds the size limit")
            data = path.read_bytes()
        else:
            data = source
        if len(data) > LIMITS.archive_bytes:
            raise FlexcilError("limit_exceeded", "Archive exceeds the size limit")
        entries, infos, total, duplicates = {}, {}, 0, []
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                members = archive.infolist()
                if len(members) > LIMITS.entries:
                    raise FlexcilError("limit_exceeded", "Archive has too many entries")
                for item in members:
                    name = item.filename
                    if not safe_name(name) or stat.S_ISLNK(item.external_attr >> 16):
                        raise FlexcilError("unsafe_archive", "Unsafe archive entry", entry=name)
                    if name in infos:
                        if item.is_dir() and infos[name].is_dir():
                            continue
                        if name != ".itemInfo":
                            raise FlexcilError("unsafe_archive", "Duplicate archive entry", entry=name)
                        # Native exports can append another .itemInfo. Retain both bytes;
                        # the final record is the one ZIP readers normally resolve.
                        duplicates.append((infos[name], entries[name]))
                    if item.flag_bits & 1:
                        raise FlexcilError("unsupported_encryption", "Encrypted flx archives are unsupported")
                    total += item.file_size
                    if item.file_size > LIMITS.entry_bytes or total > LIMITS.expanded_bytes:
                        raise FlexcilError("limit_exceeded", "Expanded archive exceeds limits")
                    infos[name] = item
                    if not item.is_dir():
                        with archive.open(item) as handle:
                            payload = handle.read(LIMITS.entry_bytes + 1)
                        if len(payload) != item.file_size:
                            raise FlexcilError("invalid_archive", "Entry size does not match its header")
                        entries[name] = payload
                document = cls(entries, hashlib.sha256(data).hexdigest(), infos, archive.comment, duplicates)
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, zlib.error, EOFError) as exc:
            raise FlexcilError("invalid_archive", f"Cannot read flx: {exc}") from exc
        document.validate()
        return document

    def get_json(self, name: str, default: Any = None) -> Any:
        if name not in self.entries:
            return default
        return parse_json(self.entries[name], name)

    @property
    def info(self) -> dict:
        return self.get_json("info")

    @property
    def pages(self) -> list[dict]:
        return self.get_json("pages.index")

    def page(self, key: str) -> dict:
        for page in self.pages:
            if page["key"] == key:
                return page
        raise FlexcilError("page_not_found", "Page UUID was not found", page_id=key)

    def objects(self, page_id: str) -> dict[str, list[dict]]:
        self.page(page_id)
        return {layer: self.get_json(f"objects/{page_id}.{layer}", []) for layer in (*LAYERS, "objects")}

    def validate(self) -> None:
        info, pages = self.info, self.pages
        if not isinstance(info, dict) or not isinstance(info.get("key"), str) or not info["key"]:
            raise FlexcilError("invalid_document", "Document info.key is missing")
        if not isinstance(pages, list) or not pages:
            raise FlexcilError("invalid_document", "Document needs a non-empty pages.index")
        page_keys = set()
        for page in pages:
            if not isinstance(page, dict) or not isinstance(page.get("key"), str) or not safe_name(page["key"]) or "/" in page["key"]:
                raise FlexcilError("invalid_document", "Invalid page identifier")
            key = page["key"]
            if key in page_keys:
                raise FlexcilError("invalid_document", "Duplicate page identifier")
            page_keys.add(key)
            frame = page.get("frame", {})
            if not isinstance(frame, dict):
                raise FlexcilError("invalid_document", "Invalid page frame")
            for axis in ("width", "height"):
                v = finite_number(frame.get(axis), f"page.{axis}", 0.001)
                if v > 1_000_000:
                    raise FlexcilError("limit_exceeded", "Page dimensions are too large")
            attachment = page.get("attachmentPage")
            if attachment:
                if not isinstance(attachment, dict) or not isinstance(attachment.get("file"), str):
                    raise FlexcilError("invalid_document", "Invalid attachment reference")
                name = "attachment/PDF/" + attachment["file"]
                index = attachment.get("index")
                if name not in self.entries or not self.entries[name].startswith(b"%PDF-"):
                    raise FlexcilError("missing_attachment", "Page references a missing PDF")
                if type(index) is not int or index < 0:
                    raise FlexcilError("invalid_document", "Invalid PDF page index")
            seen = set()
            for layer in LAYERS:
                rows = self.get_json(f"objects/{key}.{layer}", [])
                if not isinstance(rows, list):
                    raise FlexcilError("invalid_document", f"{layer} must be an array")
                for obj in rows:
                    if not isinstance(obj, dict) or not isinstance(obj.get("key"), str) or not obj["key"]:
                        raise FlexcilError("invalid_document", "Invalid object identifier")
                    if obj["key"] in seen:
                        raise FlexcilError("invalid_document", "Duplicate object identifier on page")
                    seen.add(obj["key"])
                    if layer == "drawings":
                        decode_points(obj.get("points"))
            refs = self.get_json(f"objects/{key}.objects", [])
            if not isinstance(refs, list):
                raise FlexcilError("invalid_document", "Object references must be an array")
            ref_ids = set()
            for ref in refs:
                if not isinstance(ref, dict) or not isinstance(ref.get("key"), str) or ref["key"] in ref_ids:
                    raise FlexcilError("invalid_document", "Invalid or duplicate object reference")
                ref_ids.add(ref["key"])

    def to_bytes(self) -> bytes:
        self.validate()
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.comment = self.zip_comment
            for info, data in self.duplicate_metadata:
                archive.writestr(info, data)
            for name, data in self.entries.items():
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", message="Duplicate name: '.itemInfo'", category=UserWarning)
                    archive.writestr(self.zip_info.get(name, name), data)
        return output.getvalue()

    def summary(self) -> dict:
        pages = []
        for ordinal, page in enumerate(self.pages, 1):
            layers = self.objects(page["key"])
            pages.append({"page_id": page["key"], "number": ordinal, "frame": page["frame"], "rotation": page.get("rotate", 0), "objects": {k: len(v) for k, v in layers.items() if k != "objects"}, "text": "\n".join(str(t.get("text", "")) for t in layers["texts"])})
        return {"document_id": self.info["key"], "title": self.info.get("name", ""), "format_version": self.info.get("version"), "modified_at": self.info.get("modifiedDate"), "sha256": self.original_sha256, "pages": pages}


def catalog(data: bytes, files: list[dict] | None = None, include_removed: bool = False) -> dict:
    root = decode_list(data)
    nodes = root if isinstance(root, list) else root.get("children") if isinstance(root, dict) else None
    if not isinstance(nodes, list):
        raise FlexcilError("invalid_list", "List root must contain nodes")
    file_index = {}
    for item in files or []:
        name = item.get("name") or item.get("title", "")
        if name.lower().endswith(".flx"):
            file_index.setdefault(name[:-4].upper(), []).append(item)
    documents, stack, seen_nodes, referenced = [], [(node, [], False, 0) for node in reversed(nodes)], 0, set()
    while stack:
        node, path, removed, depth = stack.pop()
        seen_nodes += 1
        if seen_nodes > 100_000 or depth > 100:
            raise FlexcilError("limit_exceeded", "Document list is too deep or large")
        if not isinstance(node, dict):
            raise FlexcilError("invalid_list", "List node must be an object")
        removed = removed or node.get("state") == "removed"
        name = str(node.get("name", ""))
        doc_id = node.get("document")
        if isinstance(doc_id, str) and doc_id:
            normalized = doc_id.rsplit("/", 1)[-1].upper().removesuffix(".FLX")
            referenced.add(normalized)
            if include_removed or not removed:
                documents.append({"document_id": normalized, "list_item_id": node.get("key"), "title": name, "folder": path, "removed": removed, "drive_files": file_index.get(normalized, [])})
        children = node.get("children", [])
        if not isinstance(children, list):
            raise FlexcilError("invalid_list", "List children must be an array")
        stack.extend((child, path + ([name] if name else []), removed, depth + 1) for child in reversed(children))
    return {"documents": documents, "unreferenced_files": [item for key, items in file_index.items() if key not in referenced for item in items]}
