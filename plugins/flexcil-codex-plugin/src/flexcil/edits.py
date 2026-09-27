"""Transactional edits: original bytes are immutable; unknown entries survive."""
from __future__ import annotations

import copy
import hashlib
import io
import math
import time
import uuid
from pathlib import Path

from .codec import Document, FlexcilError, LAYERS, finite_number, json_bytes


def identifier(value=None):
    if value is None:
        return str(uuid.uuid4()).upper()
    try:
        return str(uuid.UUID(value)).upper()
    except (ValueError, TypeError, AttributeError) as exc:
        raise FlexcilError("invalid_id", "New identifiers must be UUIDs") from exc


def frame(value):
    if not isinstance(value, dict) or set(value) != {"x", "y", "width", "height"}:
        raise FlexcilError("invalid_frame", "frame needs x, y, width, height in page points")
    result = {k: finite_number(v, f"frame.{k}", 0.001 if k in ("width", "height") else None) for k, v in value.items()}
    if any(abs(v) > 1_000_000 for v in result.values()):
        raise FlexcilError("limit_exceeded", "Frame dimensions exceed limits")
    return result


def native_text(page, operation, key):
    text = operation.get("text")
    if not isinstance(text, str) or len(text) > 1_000_000:
        raise FlexcilError("invalid_text", "text must be a string of at most one million characters")
    f = frame(operation["frame"])
    width, height = page["frame"]["width"], page["frame"]["height"]
    # Flexcil text boxes use x/width and y/height. Font size uses page width.
    normalized = {"x": f["x"] / width, "y": f["y"] / height, "width": f["width"] / width, "height": f["height"] / height}
    size = finite_number(operation.get("font_size", 18), "font_size", 1)
    if size > 1000:
        raise FlexcilError("invalid_value", "font_size must be at most 1000")
    style = {"font-family": "Helvetica, Helvetica-Light", "font-size": size / width}
    return {"key": key, "columns": [{"p": {"span": [{"style": style, "text": text}], "reflow": [], "offset": 1}}], "rotate": 0, "version": "0.0.2", "frame": normalized, "text": text, "type": 30}


def _put(document, page_id, layer, rows):
    document.entries[f"objects/{page_id}.{layer}"] = json_bytes(rows)


def _new_pages(document, operation, pages):
    import pypdfium2 as pdfium
    if operation["op"] == "insert_pdf":
        path = Path(operation["pdf"])
        if path.stat().st_size > 256 * 1024 * 1024:
            raise FlexcilError("limit_exceeded", "PDF is too large")
        data = path.read_bytes()
    else:
        from reportlab.pdfgen.canvas import Canvas
        width = finite_number(operation.get("width", 595), "width", 1)
        height = finite_number(operation.get("height", 842), "height", 1)
        if max(width, height) > 14400:
            raise FlexcilError("limit_exceeded", "Blank page is too large")
        stream = io.BytesIO()
        canvas = Canvas(stream, pagesize=(width, height))
        canvas.showPage()
        canvas.save()
        data = stream.getvalue()
    try:
        with pdfium.PdfDocument(data) as pdf:
            selected = operation.get("pages", list(range(1, len(pdf) + 1)))
            if not isinstance(selected, list) or not selected or len(selected) > 10000:
                raise FlexcilError("invalid_pages", "Select 1–10000 PDF pages (one-based)")
            if any(type(n) is not int or not 1 <= n <= len(pdf) for n in selected):
                raise FlexcilError("invalid_pages", "PDF page number is outside the document")
            sizes = [pdf.get_page_size(n - 1) for n in selected]
    except pdfium.PdfiumError as exc:
        raise FlexcilError("invalid_pdf", "Cannot open PDF; decrypt password-protected files first") from exc
    attachment = identifier(operation.get("attachment_id"))
    attachment_path = f"attachment/PDF/{attachment}"
    if attachment_path in document.entries:
        raise FlexcilError("duplicate_id", "Attachment UUID already exists")
    keys = operation.get("page_ids", [identifier() for _ in selected])
    if not isinstance(keys, list) or len(keys) != len(selected):
        raise FlexcilError("invalid_pages", "page_ids must match selected PDF pages")
    keys = [identifier(k) for k in keys]
    known = {p["key"] for p in pages}
    if len(set(keys)) != len(keys) or known.intersection(keys):
        raise FlexcilError("duplicate_id", "Page UUID already exists")
    new = [{"key": key, "frame": {"x": 0, "y": 0, "width": size[0], "height": size[1]}, "rotate": 0, "version": "0.0.1", "attachmentPage": {"file": attachment, "index": number - 1}, "annotationSubtypes": []} for key, number, size in zip(keys, selected, sizes)]
    after = operation.get("after")
    if after is None:
        position = len(pages)
    elif after == "start":
        position = 0
    else:
        document.page(after)
        position = next(i for i, p in enumerate(pages) if p["key"] == after) + 1
    pages[position:position] = new
    document.entries[attachment_path] = data
    info = document.info
    info.setdefault("attachments", {})[attachment] = Path(operation.get("pdf", "blank.pdf")).name
    document.entries["info"] = json_bytes(info)
    return keys


def apply_edits(source: Document, plan: dict) -> tuple[Document, dict]:
    if not isinstance(plan, dict) or plan.get("schema") != 1:
        raise FlexcilError("invalid_plan", "Edit plan needs schema: 1")
    if plan.get("expected_sha256") != source.original_sha256 or not source.original_sha256:
        raise FlexcilError("stale_source", "expected_sha256 must match the downloaded source")
    operations = plan.get("operations")
    if not isinstance(operations, list) or not 1 <= len(operations) <= 10000:
        raise FlexcilError("invalid_plan", "Plan must contain 1–10000 operations")
    if source.info.get("version") not in ("0.0.4", "0.0.5"):
        raise FlexcilError("unsupported_version", "Writing this document format has not been validated", version=source.info.get("version"))
    result = copy.deepcopy(source)
    applied = []
    for op in operations:
        if not isinstance(op, dict):
            raise FlexcilError("invalid_plan", "Each operation must be an object")
        name = op.get("op")
        pages = result.pages
        if name == "rename":
            title = op.get("title")
            if not isinstance(title, str) or not title.strip() or len(title) > 1024 or "\0" in title:
                raise FlexcilError("invalid_title", "Title must contain 1–1024 non-null characters")
            info = result.info
            info["name"] = title
            result.entries["info"] = json_bytes(info)
            applied.append({"op": name, "title": title, "requires_library_rename": True})
            continue
        if name in ("insert_pdf", "add_blank_page"):
            keys = _new_pages(result, op, pages)
            result.entries["pages.index"] = json_bytes(pages)
            applied.append({"op": name, "page_ids": keys})
            continue
        if name == "reorder_pages":
            keys = op.get("page_ids")
            if not isinstance(keys, list) or len(keys) != len(pages) or set(keys) != {p["key"] for p in pages}:
                raise FlexcilError("invalid_pages", "reorder_pages requires every page UUID exactly once")
            by_key = {p["key"]: p for p in pages}
            result.entries["pages.index"] = json_bytes([by_key[k] for k in keys])
        elif name == "remove_page":
            key = op.get("page_id")
            result.page(key)
            if len(pages) == 1:
                raise FlexcilError("last_page", "Cannot remove the last page")
            result.entries["pages.index"] = json_bytes([p for p in pages if p["key"] != key])
            # Orphan assets and layers are intentionally retained, including unknown formats.
            info = result.info
            if info.get("currentPage") == key:
                info["currentPage"] = result.pages[0]["key"]
                result.entries["info"] = json_bytes(info)
        elif name in ("add_text", "update_text", "remove_object"):
            key = op.get("page_id")
            page, layers = result.page(key), result.objects(key)
            object_id = op.get("object_id")
            if name == "add_text":
                object_id = identifier(object_id)
                if any(o["key"] == object_id for rows in layers.values() for o in rows):
                    raise FlexcilError("duplicate_id", "Object UUID already exists")
                obj = native_text(page, op, object_id)
                layers["texts"].append(obj)
                layers["objects"].append({k: obj[k] for k in ("key", "type", "version")})
                _put(result, key, "texts", layers["texts"])
            else:
                found = [(layer, obj) for layer in LAYERS for obj in layers[layer] if obj["key"] == object_id]
                if len(found) != 1:
                    raise FlexcilError("object_not_found", "Object UUID was not found uniquely on page")
                layer, obj = found[0]
                if name == "remove_object":
                    _put(result, key, layer, [o for o in layers[layer] if o["key"] != object_id])
                    layers["objects"] = [o for o in layers["objects"] if o["key"] != object_id]
                else:
                    if layer != "texts":
                        raise FlexcilError("wrong_object_type", "update_text requires a text object")
                    # Keep the first span's style; replacement text is deliberately plain.
                    try:
                        old_style = copy.deepcopy(obj["columns"][0]["p"]["span"][0]["style"])
                    except (KeyError, IndexError, TypeError):
                        old_style = None
                    f = obj.get("frame", {})
                    op = {**op, "frame": op.get("frame", {"x": f.get("x", 0) * page["frame"]["width"], "y": f.get("y", 0) * page["frame"]["height"], "width": f.get("width", .5) * page["frame"]["width"], "height": f.get("height", .1) * page["frame"]["height"]})}
                    replacement = native_text(page, op, object_id)
                    if old_style is not None:
                        if "font_size" in op:
                            old_style["font-size"] = op["font_size"] / page["frame"]["width"]
                        replacement["columns"][0]["p"]["span"][0]["style"] = old_style
                    obj.update({k: replacement[k] for k in ("text", "columns", "frame")})
                    _put(result, key, "texts", layers["texts"])
            _put(result, key, "objects", layers["objects"])
            applied.append({"op": name, "page_id": key, "object_id": object_id})
            continue
        else:
            raise FlexcilError("unsupported_operation", "Unknown edit operation", operation=name)
        applied.append({"op": name})
    info = result.info
    previous = finite_number(info.get("modifiedDate", 0), "modifiedDate")
    info["modifiedDate"] = max(math.ceil(time.time() * 1000) / 1000, previous + .001)
    result.entries["info"] = json_bytes(info)
    result.validate()
    return result, {"schema": 1, "source_sha256": source.original_sha256, "document_id": info["key"], "operations": applied, "diff": diff_documents(source, result)}


def diff_documents(before: Document, after: Document) -> dict:
    old, new = set(before.entries), set(after.entries)
    old_pages, new_pages = [p["key"] for p in before.pages], [p["key"] for p in after.pages]
    objects = {}
    for page in set(old_pages) | set(new_pages):
        def index(doc, available):
            return {obj["key"]: obj for layer in LAYERS for obj in doc.objects(page)[layer]} if page in available else {}
        a, b = index(before, old_pages), index(after, new_pages)
        objects[page] = {"added": sorted(b.keys() - a.keys()), "removed": sorted(a.keys() - b.keys()), "changed": sorted(k for k in a.keys() & b.keys() if a[k] != b[k]), "preserved": sum(a[k] == b[k] for k in a.keys() & b.keys())}
    return {"same_document": before.info["key"] == after.info["key"], "entries_added": sorted(new - old), "entries_removed": sorted(old - new), "entries_changed": sorted(k for k in old & new if before.entries[k] != after.entries[k]), "entries_preserved": sum(before.entries[k] == after.entries[k] for k in old & new), "pages_before": old_pages, "pages_after": new_pages, "objects": objects}
