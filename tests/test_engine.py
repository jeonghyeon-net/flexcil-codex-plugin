import base64
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from reportlab.pdfgen.canvas import Canvas
from flexcil.codec import Document, FlexcilError, decode_list, encode_list, decode_points, json_bytes, catalog
from flexcil.edits import apply_edits, diff_documents
from flexcil.library import Library
from flexcil.render import render_page, export_pdf
from flexcil import state
from flexcil.cli import main
from flexcil.handoff import prepare_import, resolve_import, placement_plan
import contextlib
import warnings

DOC = "00000000-0000-4000-8000-000000000001"
PAGE = "00000000-0000-4000-8000-000000000002"
INK = "00000000-0000-4000-8000-000000000003"
TEXT = "00000000-0000-4000-8000-000000000004"
FOLDER = "00000000-0000-4000-8000-000000000005"


def fixture():
    out = io.BytesIO()
    pdf = Canvas(out, pagesize=(595, 842))
    pdf.drawString(36, 800, "Synthetic Flexcil fixture")
    pdf.showPage(); pdf.save()
    points = base64.b64encode(struct.pack("<Iffffff", 2, 0, 0, .1, .2, .1, .1)).decode()
    entries = {"info": json_bytes({"key": DOC, "name": "Synthetic", "version": "0.0.5", "modifiedDate": 100, "attachments": {DOC: ""}, "currentPage": PAGE}),
               "pages.index": json_bytes([{"key": PAGE, "version": "0.0.5", "frame": {"width": 595, "height": 842}, "rotate": 0, "attachmentPage": {"file": DOC, "index": 0}}]),
               "attachment/PDF/" + DOC: out.getvalue(), "future/opaque": b"\x00preserve-me\xff",
               "objects/" + PAGE + ".drawings": json_bytes([{"key": INK, "type": 1, "points": points, "start": {"x": .1, "y": .1}, "scale": {"x": 1, "y": 1}, "rotate": 0, "strokeColor": 0xFF000000}]),
               "objects/" + PAGE + ".objects": json_bytes([{"key": INK, "type": 1}])}
    return Document.load(Document(entries).to_bytes())


def text_plan(doc):
    return {"schema": 1, "expected_sha256": doc.original_sha256, "operations": [{"op": "add_text", "page_id": PAGE, "object_id": TEXT, "text": "한글 UTF-8 roundtrip", "frame": {"x": 40, "y": 200, "width": 500, "height": 50}}]}


class CodecTests(unittest.TestCase):
    def test_unknown_bytes_and_ink_survive(self):
        doc = fixture(); after, report = apply_edits(doc, text_plan(doc)); after = Document.load(after.to_bytes())
        for name, data in doc.entries.items():
            if name not in ("info", "objects/" + PAGE + ".objects"):
                self.assertEqual(data, after.entries[name])
        self.assertEqual(after.objects(PAGE)["texts"][0]["text"], "한글 UTF-8 roundtrip")
        self.assertEqual(report["diff"]["objects"][PAGE]["preserved"], 1)

    def test_no_source_mutation_on_failure(self):
        doc = fixture(); original = doc.to_bytes(); plan = text_plan(doc)
        plan["operations"].append({"op": "unknown"})
        with self.assertRaises(FlexcilError): apply_edits(doc, plan)
        self.assertEqual(doc.to_bytes(), original)

    def test_stale_source(self):
        doc = fixture(); plan = text_plan(doc); plan["expected_sha256"] = "wrong"
        with self.assertRaisesRegex(FlexcilError, "expected_sha256"): apply_edits(doc, plan)

    def test_unknown_version_readable_not_writable(self):
        doc = fixture(); info = doc.info; info["version"] = "9"; doc.entries["info"] = json_bytes(info)
        doc = Document.load(doc.to_bytes())
        with self.assertRaises(FlexcilError): apply_edits(doc, text_plan(doc))

    def test_duplicate_object_refused(self):
        doc = fixture(); plan = text_plan(doc); plan["operations"][0]["object_id"] = INK
        with self.assertRaises(FlexcilError): apply_edits(doc, plan)

    def test_page_add_reorder_remove(self):
        doc = fixture(); plan = {"schema": 1, "expected_sha256": doc.original_sha256, "operations": [{"op": "add_blank_page", "page_ids": [TEXT], "after": "start"}, {"op": "reorder_pages", "page_ids": [PAGE, TEXT]}, {"op": "remove_page", "page_id": PAGE}]}
        after, _ = apply_edits(doc, plan)
        self.assertEqual(after.info["currentPage"], TEXT)
        self.assertEqual(len(after.pages), 1)
        self.assertEqual(after.entries["future/opaque"], doc.entries["future/opaque"])

    def test_last_page_protected(self):
        doc = fixture(); plan = text_plan(doc); plan["operations"] = [{"op": "remove_page", "page_id": PAGE}]
        with self.assertRaises(FlexcilError): apply_edits(doc, plan)

    def test_inserted_pdf_attachment_value_is_password_not_filename(self):
        import pypdfium2 as pdfium
        doc = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "filename-must-not-be-a-password.pdf"
            path.write_bytes(doc.entries["attachment/PDF/" + DOC])
            for op in ({"op": "insert_pdf", "pdf": str(path)}, {"op": "add_blank_page"}):
                after, _ = apply_edits(doc, {"schema": 1, "expected_sha256": doc.original_sha256, "operations": [op]})
                after = Document.load(after.to_bytes())
                attachment_id = after.pages[-1]["attachmentPage"]["file"]
                password = after.info["attachments"][attachment_id]
                self.assertEqual(password, "")
                self.assertEqual(after.info["attachments"][DOC], "")
                with pdfium.PdfDocument(after.entries["attachment/PDF/" + attachment_id], password=password) as pdf:
                    self.assertEqual(len(pdf), 1)

    def test_remove_object(self):
        doc = fixture(); plan = text_plan(doc); plan["operations"] = [{"op": "remove_object", "page_id": PAGE, "object_id": INK}]
        after, _ = apply_edits(doc, plan)
        self.assertEqual(after.objects(PAGE)["drawings"], [])
        self.assertEqual(after.objects(PAGE)["objects"], [])

    def test_zip_traversal(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as z: z.writestr("../escape", "x")
        with self.assertRaises(FlexcilError): Document.load(stream.getvalue())

    def test_native_duplicate_metadata_preserved(self):
        doc = fixture(); stream = io.BytesIO(doc.to_bytes())
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(stream, "a") as z:
                z.writestr(".itemInfo", encode_list({"originKey": "first"}))
                z.writestr(".itemInfo", encode_list({"originKey": "second"}))
        loaded = Document.load(stream.getvalue())
        after, _ = apply_edits(loaded, text_plan(loaded))
        final = Document.load(after.to_bytes())
        self.assertEqual([data for _,data in loaded.duplicate_metadata], [data for _,data in final.duplicate_metadata])
        self.assertEqual(decode_list(final.entries[".itemInfo"])["originKey"], "second")

    def test_duplicate_content_rejected(self):
        stream = io.BytesIO(fixture().to_bytes())
        with zipfile.ZipFile(stream, "a") as z:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning); z.writestr("info", b"{}")
        with self.assertRaises(FlexcilError): Document.load(stream.getvalue())

    def test_bad_points(self):
        for raw in (b"", struct.pack("<I", 9999999), struct.pack("<Ifff", 1, float("nan"), 0, 0)):
            with self.assertRaises(FlexcilError): decode_points(base64.b64encode(raw).decode())

    def test_list_bounded(self):
        value = [{"name": "한글", "key": "a"}]; encoded = encode_list(value)
        self.assertEqual(decode_list(encoded), value)
        for bad in (encoded + b"junk", encoded[:-1], struct.pack("<Q", 2**40) + encoded[8:]):
            with self.assertRaises(FlexcilError): decode_list(bad)

    def test_catalog_id_domains_and_removed(self):
        data = encode_list([{"key": "folder", "name": "F", "children": [{"key": "list-item", "name": "D", "document": DOC + ".FLX"}]}, {"key": "r", "document": "REMOVED", "state": "removed"}])
        c = catalog(data, [{"id": "drive-id", "title": DOC + ".flx"}, {"id": "trash-file", "name": "REMOVED.flx"}])
        self.assertEqual(c["documents"][0]["drive_files"][0]["id"], "drive-id")
        self.assertEqual(c["documents"][0]["list_item_id"], "list-item")
        self.assertEqual(c["unreferenced_files"], [])


class LibraryTests(unittest.TestCase):
    def library(self):
        return Library(encode_list([{"key": TEXT, "document": DOC, "state": "valid", "name": "D", "type": 4, "unknown": 42}]), encode_list([]))

    def edit(self, lib, ops):
        result, report = lib.edit({"schema": 1, "expected_sha256": lib.source_sha256, "expected_trash_sha256": lib.trash_sha256, "operations": ops})
        data = result.bytes()
        return Library(data["active"], data["trash"]), report

    def test_crud_folders_and_documents(self):
        lib = self.library()
        moved, _ = self.edit(lib, [{"op": "create_folder", "item_id": FOLDER, "title": "Folder"}, {"op": "move", "item_id": TEXT, "parent_id": FOLDER}, {"op": "rename", "item_id": TEXT, "title": "Renamed"}])
        item = moved.find(TEXT)[0]
        self.assertEqual(item["unknown"], 42)
        self.assertEqual(moved.find(TEXT)[2][0]["key"], FOLDER)
        trash, report = self.edit(moved, [{"op": "trash", "item_id": TEXT}])
        self.assertEqual(report["upload_order"], ["trash", "active"])
        restored, report = self.edit(trash, [{"op": "restore", "item_id": TEXT, "parent_id": FOLDER}])
        self.assertEqual(report["upload_order"], ["active", "trash"])
        self.assertEqual(restored.find(TEXT)[0]["name"], "Renamed")
        self.assertEqual(lib.active[0]["name"], "D")

    def test_cycles_rejected(self):
        lib, _ = self.edit(self.library(), [{"op": "create_folder", "item_id": FOLDER, "title": "Folder"}])
        with self.assertRaises(FlexcilError): self.edit(lib, [{"op": "move", "item_id": FOLDER, "parent_id": FOLDER}])

    def test_stale_trash_rejected(self):
        lib = self.library()
        with self.assertRaises(FlexcilError): lib.edit({"schema": 1, "expected_sha256": lib.source_sha256, "operations": [{"op": "trash", "item_id": TEXT}]})


class RenderTests(unittest.TestCase):
    def test_background_and_ink_preview(self):
        image, report = render_page(fixture(), PAGE, scale=1)
        self.assertEqual(image.size, (595, 842))
        self.assertTrue(report["warnings"])
        self.assertNotEqual(image.getpixel((65, 63)), (255, 255, 255))

    def test_export_is_valid_pdf(self):
        import pypdfium2 as pdfium
        data, report = export_pdf(fixture(), scale=1)
        with pdfium.PdfDocument(data) as pdf:
            self.assertEqual(len(pdf), 1)
        self.assertTrue(report["rasterized"])

    def test_partial_export_not_silent(self):
        with self.assertRaises(FlexcilError): export_pdf(fixture(), strict=True)

    def test_render_limit(self):
        with self.assertRaises(FlexcilError): render_page(fixture(), PAGE, scale=100)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"FLEXCIL_STATE_DIR": str(self.root/"state")})
        self.env.start()
        self.doc = fixture(); self.input = self.root/"source.flx"; self.input.write_bytes(self.doc.to_bytes())
        self.doc = Document.load(self.input)
        state.configure("test", "synthetic-folder", "android")

    def tearDown(self):
        self.env.stop(); self.temp.cleanup()

    def prepare(self):
        return state.prepare(self.input, text_plan(self.doc), "test", "synthetic-file", probe=True)

    def test_probe_required(self):
        with self.assertRaises(FlexcilError): state.prepare(self.input, text_plan(self.doc), "test", "synthetic-file")

    def test_receipt_preflight_readback(self):
        r = self.prepare(); op = r["operation_id"]
        self.assertEqual(state.preflight(op, self.input)["state"], "ready")
        self.assertEqual(state.verify(op, r["candidate_path"])["state"], "verified")
        self.assertTrue(state.preflight(op, r["candidate_path"])["already_uploaded"])

    def test_changed_candidate_blocked(self):
        r = self.prepare(); Path(r["candidate_path"]).write_bytes(b"changed")
        with self.assertRaises(FlexcilError): state.preflight(r["operation_id"], self.input)

    def test_remote_change_blocks_rollback(self):
        r = self.prepare()
        with self.assertRaises(FlexcilError): state.rollback_candidate(r["operation_id"], self.input, self.root/"restore.flx")

    def test_output_does_not_overwrite(self):
        with self.assertRaises(FlexcilError): state.write_file(self.input, b"oops")
        self.assertEqual(Document.load(self.input).info["key"], DOC)

    def test_profile_path_traversal(self):
        with self.assertRaises(FlexcilError): state.configure("../escape", "some-folder")

    def test_roundtrip_requires_device_change(self):
        r = self.prepare()
        with self.assertRaises(FlexcilError): state.verify(r["operation_id"], r["candidate_path"], device_confirmed=True)
        self.assertFalse(state.load_profile("test")["sync_verified"])
        candidate = Document.load(r["candidate_path"])
        objects = candidate.objects(PAGE)
        stroke = dict(objects["drawings"][0]); stroke["key"] = FOLDER
        objects["drawings"].append(stroke); objects["objects"].append({"key": FOLDER, "type": 1})
        for layer in ("drawings", "objects"):
            candidate.entries[f"objects/{PAGE}.{layer}"] = json_bytes(objects[layer])
        remote = self.root/"tablet.flx"; remote.write_bytes(candidate.to_bytes())
        result = state.verify(r["operation_id"], remote, device_confirmed=True)
        self.assertEqual(result["state"], "device_verified")
        self.assertTrue(state.load_profile("test")["sync_verified"])

    def test_handoff_requires_registration_and_matches_pdf(self):
        pdf = self.root/"report.pdf"; pdf.write_bytes(self.doc.entries["attachment/PDF/" + DOC])
        request = prepare_import(pdf, "PR report", ["Engineering", "Reviews"], self.root/"job")
        self.assertEqual(request["state"], "awaiting_native_import")
        listing = self.root/"documents.list"
        listing.write_bytes(encode_list([{"key": TEXT, "name": "Imported", "type": 4, "document": DOC, "state": "valid"}]))
        result = resolve_import(self.root/"job/request.json", self.input, listing)
        self.assertEqual(result["state"], "registered_needs_placement")
        after, _ = Library(listing.read_bytes()).edit(result["library_plan"])
        self.assertEqual([n["name"] for n in after.find(TEXT)[2]], ["Engineering", "Reviews"])

    def test_cli_machine_errors_and_no_clobber(self):
        plan = self.root/"plan.json"; plan.write_bytes(json_bytes(text_plan(self.doc)))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["edit", str(self.input), "--plan", str(plan), "--output", str(self.input)])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["error"]["code"], "output_exists")

    def test_library_cli_keeps_before_copies(self):
        listing = self.root/"documents.list"; listing.write_bytes(encode_list([]))
        lib = Library(listing.read_bytes()); plan = self.root/"plan.json"
        plan.write_bytes(json_bytes({"schema": 1, "expected_sha256": lib.source_sha256, "operations": [{"op": "create_folder", "title": "Test"}]}))
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(["library-edit", str(listing), "--plan", str(plan), "--output-dir", str(self.root/"operation")])
        self.assertEqual(code, 0)
        self.assertEqual((self.root/"operation/documents.list.before").read_bytes(), listing.read_bytes())


if __name__ == "__main__": unittest.main()
