"""Flexcil's virtual folders and trash. Drive parents are a separate namespace."""
from __future__ import annotations

import copy
import hashlib
import time

from .codec import FlexcilError, decode_list, encode_list
from .edits import identifier


class Library:
    def __init__(self, active_data: bytes, trash_data: bytes | None = None):
        self.active = decode_list(active_data)
        self.trash = decode_list(trash_data) if trash_data is not None else []
        self.source_sha256 = hashlib.sha256(active_data).hexdigest()
        self.trash_sha256 = hashlib.sha256(trash_data).hexdigest() if trash_data is not None else None
        self.validate()

    @staticmethod
    def _walk(nodes, ancestors=()):
        if len(ancestors) > 100:
            raise FlexcilError("limit_exceeded", "Library nesting exceeds 100")
        for node in nodes:
            if not isinstance(node, dict) or not isinstance(node.get("key"), str):
                raise FlexcilError("invalid_library", "Every library item needs a key")
            yield node, nodes, ancestors
            children = node.get("children", [])
            if not isinstance(children, list):
                raise FlexcilError("invalid_library", "Folder children must be an array")
            yield from Library._walk(children, ancestors + (node,))

    def validate(self):
        if not isinstance(self.active, list) or not isinstance(self.trash, list):
            raise FlexcilError("invalid_library", "Library and trash roots must be arrays")
        seen = set()
        for node, _, _ in self._walk(self.active):
            if node["key"] in seen:
                raise FlexcilError("invalid_library", "Duplicate active library item key")
            seen.add(node["key"])
            if len(seen) > 100000:
                raise FlexcilError("limit_exceeded", "Library exceeds 100000 items")
        trash_seen = set()
        for record in self.trash:
            if not isinstance(record, dict) or not isinstance(record.get("item"), dict) or not isinstance(record.get("parentInfo", []), list):
                raise FlexcilError("invalid_library", "Malformed trash record")
            for node, _, _ in self._walk([record["item"]]):
                if node["key"] in trash_seen:
                    raise FlexcilError("invalid_library", "Duplicate trash item key")
                trash_seen.add(node["key"])

    def find(self, key):
        found = [row for row in self._walk(self.active) if row[0]["key"] == key and row[0].get("state") != "removed" and all(p.get("state") != "removed" for p in row[2])]
        if len(found) != 1:
            raise FlexcilError("item_not_found", "Active library item key was not found uniquely", item_id=key)
        return found[0]

    def destination(self, key):
        if key is None or key == "root":
            return self.active, None
        node, _, _ = self.find(key)
        if node.get("type") != 1 or node.get("document"):
            raise FlexcilError("not_folder", "Destination must be a Flexcil folder")
        return node.setdefault("children", []), node

    @staticmethod
    def title(value):
        if not isinstance(value, str) or not value.strip() or len(value) > 1024 or "\0" in value:
            raise FlexcilError("invalid_title", "Title must contain 1–1024 non-null characters")
        return value

    def tree(self):
        def clean(nodes):
            return [{"item_id": n["key"], "title": n.get("name", ""), "kind": "folder" if n.get("type") == 1 and not n.get("document") else "document", "document_id": n.get("document"), "state": n.get("state"), **({"children": clean(n["children"])} if "children" in n else {})} for n in nodes]
        return {"source_sha256": self.source_sha256, "trash_sha256": self.trash_sha256, "items": clean(self.active), "trash": clean([r["item"] for r in self.trash])}

    def edit(self, plan):
        if not isinstance(plan, dict) or plan.get("schema") != 1 or plan.get("expected_sha256") != self.source_sha256:
            raise FlexcilError("stale_source", "Library plan needs schema: 1 and matching expected_sha256")
        operations = plan.get("operations")
        if not isinstance(operations, list) or not 1 <= len(operations) <= 1000:
            raise FlexcilError("invalid_plan", "Library plan needs 1–1000 operations")
        names = {op.get("op") for op in operations if isinstance(op, dict)}
        uses_trash = bool(names & {"trash", "restore"})
        if uses_trash and (self.trash_sha256 is None or plan.get("expected_trash_sha256") != self.trash_sha256):
            raise FlexcilError("stale_trash", "Trash operations require a fresh .trash.list and its hash")
        if "trash" in names and "restore" in names:
            raise FlexcilError("invalid_plan", "Trash and restore must use separate transactions")
        result, report, now = copy.deepcopy(self), [], time.time()
        for op in operations:
            if not isinstance(op, dict):
                raise FlexcilError("invalid_plan", "Every operation must be an object")
            action = op.get("op")
            if action == "create_folder":
                target, parent = result.destination(op.get("parent_id"))
                title, key = self.title(op.get("title")), identifier(op.get("item_id"))
                if any(n["key"] == key for n, _, _ in result._walk(result.active)):
                    raise FlexcilError("duplicate_id", "Library item key already exists")
                if any(n.get("name") == title and n.get("state") != "removed" for n in target):
                    raise FlexcilError("ambiguous_name", "An item with this name already exists in the destination")
                target.append({"key": key, "state": "valid", "name": title, "type": 1, "favorite": False, "version": "0.0.5", "color": "Blue", "modifiedDate": now, "createDate": now, "children": []})
                if parent:
                    parent["modifiedDate"] = now
                report.append({"op": action, "item_id": key})
                continue
            if action == "restore":
                target, parent = result.destination(op.get("parent_id"))
                matches = [r for r in result.trash if r["item"]["key"] == op.get("item_id")]
                if len(matches) != 1:
                    raise FlexcilError("item_not_found", "Trash item not found uniquely")
                item = matches[0]["item"]
                existing = {n["key"] for n, _, _ in result._walk(result.active)}
                if any(n["key"] in existing for n, _, _ in result._walk([item])):
                    raise FlexcilError("duplicate_id", "Restore would duplicate an active item; recover interrupted operation first")
                item["state"], item["modifiedDate"] = "valid", now
                target.append(item)
                result.trash.remove(matches[0])
                if parent:
                    parent["modifiedDate"] = now
            elif action in ("rename", "move", "trash"):
                item, siblings, ancestors = result.find(op.get("item_id"))
                if action == "rename":
                    item["name"] = self.title(op.get("title"))
                elif action == "move":
                    target, parent = result.destination(op.get("parent_id"))
                    descendant_ids = {n["key"] for n, _, _ in result._walk([item])}
                    if parent and parent["key"] in descendant_ids:
                        raise FlexcilError("folder_cycle", "A folder cannot be moved into itself or its descendants")
                    siblings.remove(item)
                    target.append(item)
                    if parent:
                        parent["modifiedDate"] = now
                else:
                    if any(r["item"]["key"] == item["key"] for r in result.trash):
                        raise FlexcilError("duplicate_id", "Trash already contains this item; recover interrupted operation first")
                    item["state"] = "removed"
                    # Empty parentInfo is the observed native root-restore representation.
                    result.trash.append({"parentInfo": [], "item": item})
                    siblings.remove(item)
                item["modifiedDate"] = now
                for parent in ancestors:
                    parent["modifiedDate"] = now
            else:
                raise FlexcilError("unsupported_operation", "Unknown library operation", operation=action)
            report.append({"op": action, "item_id": op.get("item_id")})
        result.validate()
        order = ["trash", "active"] if "trash" in names else ["active", "trash"] if "restore" in names else ["active"]
        return result, {"schema": 1, "source_sha256": self.source_sha256, "trash_sha256": self.trash_sha256, "operations": report, "upload_order": order, "notes": ["Preserve the flx files when moving to trash", "Restore destination defaults to root", "Multi-file writes are recoverable but not atomic"] if uses_trash else []}

    def bytes(self):
        self.validate()
        return {"active": encode_list(self.active), "trash": encode_list(self.trash)}
