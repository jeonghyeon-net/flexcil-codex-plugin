import zipfile
from unittest.mock import patch

from flexcil.codec import Document, FlexcilError, encode_list
from flexcil.connection import discover_config
from flexcil.creation import build_document
from flexcil.direct import Drive
from flexcil.library import Library
from flexcil.transactions import prepare_update, publish_update
from flexcil import state
from test_direct import PrivateTest, FakeDrive


class ConnectionTests(PrivateTest):
    def test_apk_public_config_discovery_does_not_extract_files(self):
        path = self.root / "sample.apk"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("classes.dex", b"\0".join([b"123456789-synthetic.apps.googleusercontent.com", b"com.flexcil.synthetic://"]))
            z.writestr("../../outside", b"not extracted")
        result = discover_config("test", path)
        self.assertFalse(result["signature_verified"])
        self.assertEqual(state.read_json(result["client_config"])["redirect_uri"], "com.flexcil.synthetic://")
        self.assertFalse((self.root.parent / "outside").exists())
        with zipfile.ZipFile(path, "a") as z:
            z.writestr("classes2.dex", b"123456789-second.apps.googleusercontent.com")
        with self.assertRaises(FlexcilError) as error:
            discover_config("test", path)
        self.assertEqual(error.exception.code, "ambiguous_oauth_config")

    def test_resume_rejects_changed_endpoint_before_credentials_are_read(self):
        import hashlib
        session = self.root / "session.json"
        state.write_file(session, __import__("json").dumps({"url": "https://oauth2.googleapis.com/token",
            "sha256": hashlib.sha256(b"x").hexdigest(), "metadata": {}, "file_id": None}).encode())
        with patch("flexcil.direct.access_token") as token:
            with self.assertRaises(FlexcilError) as error:
                Drive("test").upload(b"x", {}, session_path=session)
        self.assertEqual(error.exception.code, "invalid_upload_session")
        token.assert_not_called()


class UpdateDrive(FakeDrive):
    def __init__(self):
        super().__init__()
        document, item = build_document("Original")
        self.item = item
        self.data.update({"library-file": encode_list([item]), "trash-file": encode_list([]), "document-file": document.to_bytes()})
        for key, sync_id in (("library-file", "documents.list"), ("trash-file", ".trash.list"), ("document-file", document.info["key"])):
            self.meta[key] = {"id": key, "parents": ["synthetic-root"],
                "appProperties": {"Flexcil": "MakeFromFlexcil", "SyncId": sync_id}}

    def list(self, folder):
        return list(self.meta.values())


class UpdateTests(PrivateTest):
    def library_plan(self, drive, operations):
        library = Library(drive.data["library-file"], drive.data["trash-file"])
        return {"schema": 1, "expected_sha256": library.source_sha256,
                "expected_trash_sha256": library.trash_sha256, "operations": operations}

    def test_rename_updates_both_titles_and_resumes_without_repeating_writes(self):
        drive = UpdateDrive()
        plan = self.library_plan(drive, [{"op": "rename", "item_id": drive.item["key"], "title": "Renamed"}])
        receipt = prepare_update("test", plan, probe=True, drive=drive)
        result = publish_update(receipt["operation_id"], drive)
        self.assertEqual(result["state"], "cloud_verified")
        self.assertEqual(drive.uploads, ["document-file", "library-file"])
        self.assertEqual(Document.load(drive.data["document-file"]).info["name"], "Renamed")
        self.assertEqual(Library(drive.data["library-file"]).active[0]["name"], "Renamed")
        publish_update(receipt["operation_id"], drive)
        self.assertEqual(len(drive.uploads), 2)

    def test_trash_and_restore_preserve_document_bytes_and_follow_safe_order(self):
        drive = UpdateDrive()
        original = drive.data["document-file"]
        for action, expected in (("trash", ["trash-file", "library-file"]), ("restore", ["library-file", "trash-file"])):
            drive.uploads = []
            receipt = prepare_update("test", self.library_plan(drive, [{"op": action, "item_id": drive.item["key"]}]), probe=True, drive=drive)
            publish_update(receipt["operation_id"], drive)
            self.assertEqual(drive.uploads, expected)
            self.assertEqual(drive.data["document-file"], original)

    def test_late_conflict_retains_updated_document_and_never_clobbers_library(self):
        drive = UpdateDrive()
        receipt = prepare_update("test", self.library_plan(drive, [{"op": "rename", "item_id": drive.item["key"], "title": "New"}]), probe=True, drive=drive)
        drive.change_during_upload = True
        with self.assertRaises(FlexcilError) as error:
            publish_update(receipt["operation_id"], drive)
        self.assertEqual(error.exception.code, "stale_source")
        self.assertEqual(drive.uploads, ["document-file"])
        self.assertEqual(state.receipt(receipt["operation_id"])["state"], "conflict")
