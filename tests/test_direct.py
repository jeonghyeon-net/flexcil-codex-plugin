import copy
import hashlib
import json
import os
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from flexcil import state
from flexcil.codec import Document, FlexcilError, decode_list, encode_list, json_bytes
from flexcil.direct import Drive, auth_start, auth_finish, http, checked_json
from flexcil.creation import build_document, place_new, prepare_new, publish_new, verify_new
from flexcil.edits import apply_edits
from flexcil.library import Library


class PrivateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"FLEXCIL_STATE_DIR": str(self.root / "private")})
        self.env.start()
        state.configure("test", "synthetic-root", "android")

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()


class AuthTests(PrivateTest):
    def start(self):
        config = self.root / "client.json"
        config.write_text(json.dumps({"client_id": "synthetic.apps.googleusercontent.com", "redirect_uri": "example.test:/oauth"}))
        return auth_start("test", config)

    def callback(self, result, **overrides):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(result["authorization_url"]).query)
        path = self.root / "callback.txt"
        values = {"code": "synthetic-code", "state": query["state"][0], **overrides}
        path.write_text("example.test:/oauth?" + urllib.parse.urlencode(values))
        return path

    def test_pkce_login_and_private_credentials(self):
        result = self.start()
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(result["authorization_url"]).query)
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertNotIn("verifier", result)
        token = {"access_token": "synthetic-access", "refresh_token": "synthetic-refresh", "token_type": "Bearer", "expires_in": 3600}
        with patch("flexcil.direct.http", return_value=(200, {}, json_bytes(token))) as request:
            response = auth_finish("test", self.callback(result))
        self.assertEqual(response["status"], "authenticated")
        sent = urllib.parse.parse_qs(request.call_args.args[2].decode())
        self.assertIn("code_verifier", sent)
        self.assertNotIn("access_token", response)
        path = self.root / "private/connections/test/credentials.json"
        self.assertEqual(state.read_json(path)["refresh_token"], "synthetic-refresh")
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertFalse(path.with_name("pending.json").exists())

    def test_state_mismatch_never_exchanges_code(self):
        result = self.start()
        with patch("flexcil.direct.http") as request:
            with self.assertRaises(FlexcilError):
                auth_finish("test", self.callback(result, state="different"))
            request.assert_not_called()

    def test_expired_flow_never_exchanges_code(self):
        result = self.start()
        with patch("flexcil.direct.time.time", return_value=time.time() + 1000), patch("flexcil.direct.http") as request:
            with self.assertRaises(FlexcilError):
                auth_finish("test", self.callback(result))
            request.assert_not_called()

    def test_credentials_not_forwarded_to_arbitrary_host(self):
        for url in ("http://www.googleapis.com/drive", "https://example.com/", "https://www.googleapis.com@evil.test/", "https://www.googleapis.com:8443/", "https://evil@www.googleapis.com/"):
            with self.assertRaises(FlexcilError):
                http("GET", url)
        with self.assertRaises(FlexcilError) as error:
            checked_json(401, b'{"error":"synthetic-secret"}')
        self.assertNotIn("synthetic-secret", str(error.exception))

    def test_wrong_app_context_does_not_enable_registration(self):
        with patch.object(Drive, "metadata", return_value={"mimeType": "application/vnd.google-apps.folder", "appProperties": {}}):
            with self.assertRaises(FlexcilError) as error:
                Drive("test").probe()
        self.assertEqual(error.exception.code, "wrong_oauth_context")

    def test_resumable_transfer_uses_server_received_range(self):
        calls = []
        payload = b"x" * (4 * 1024 * 1024 + 10)
        session = "https://www.googleapis.com/upload/drive/v3/files?upload_id=synthetic"
        replies = iter([(200, {"Location": session}, b""), (308, {"Range": "bytes=0-4194303"}, b""), (200, {}, b'{"id":"new-file"}')])
        def send(method, url, body=None, headers=None, **kwargs):
            calls.append((method, url, len(body or b""), headers))
            return next(replies)
        with patch("flexcil.direct.access_token", return_value="synthetic"), patch("flexcil.direct.http", side_effect=send):
            result = Drive("test").upload(payload, {"name": "test.flx"}, session_path=self.root / "session.json")
        self.assertEqual(result["id"], "new-file")
        self.assertEqual(calls[2][2], 10)
        self.assertEqual(calls[2][3]["Content-Range"], f"bytes 4194304-4194313/{len(payload)}")
        with patch("flexcil.direct.access_token", return_value="synthetic"), patch("flexcil.direct.http", return_value=(200, {}, b'{"id":"new-file"}')) as request:
            Drive("test").upload(payload, {"name": "test.flx"}, session_path=self.root / "session.json")
            self.assertEqual(request.call_args.args[3]["Content-Range"], f"bytes */{len(payload)}")


class FakeDrive:
    def __init__(self):
        self.data = {"library-file": encode_list([])}
        self.meta = {"library-file": {"id": "library-file", "appProperties": {"Flexcil": "MakeFromFlexcil", "SyncId": "documents.list"}}}
        self.uploads = []
        self.change_during_upload = False

    def probe(self):
        return {"library_file_id": "library-file"}

    def generate_id(self):
        return "reserved-document-file"

    def metadata(self, key):
        if key not in self.meta:
            raise FlexcilError("google_http_error", "missing", status=404)
        return copy.deepcopy(self.meta[key])

    def download(self, key):
        return self.data[key]

    def upload(self, data, metadata, key=None, **kwargs):
        key = key or metadata["id"]
        self.uploads.append(key)
        self.meta[key] = {**self.meta.get(key, {}), **metadata}
        self.data[key] = data
        if key != "library-file" and self.change_during_upload:
            self.data["library-file"] = encode_list([{"key": "external", "type": 1, "name": "External", "children": []}])
        return self.meta[key]


class CreationTests(PrivateTest):
    def test_device_verification_requires_confirmation_and_new_content(self):
        drive = FakeDrive()
        receipt = prepare_new("test", "Report", probe=True, drive=drive)
        op = receipt["operation_id"]
        publish_new(op, drive)
        with self.assertRaises(FlexcilError) as error:
            verify_new(op, drive=drive)
        self.assertEqual(error.exception.code, "device_confirmation_required")
        with self.assertRaises(FlexcilError) as error:
            verify_new(op, device_confirmed=True, drive=drive)
        self.assertEqual(error.exception.code, "device_mark_missing")
        doc = Document.load(drive.data[receipt["file_id"]])
        edited, _ = apply_edits(doc, {"schema": 1, "expected_sha256": doc.original_sha256,
            "operations": [{"op": "add_text", "page_id": doc.pages[0]["key"], "text": "Device mark",
                            "frame": {"x": 10, "y": 10, "width": 100, "height": 30}}]})
        drive.data[receipt["file_id"]] = edited.to_bytes()
        result = verify_new(op, device_confirmed=True, drive=drive)
        self.assertTrue(result["device_verified"])
        self.assertTrue(state.load_profile("test")["new_document_verified"])

    def test_native_document_has_distinct_ids_and_correct_password_map(self):
        document, item = build_document("Report")
        loaded = Document.load(document.to_bytes())
        key = loaded.info["key"]
        self.assertNotEqual(key, item["key"])
        self.assertEqual(item["document"], key)
        self.assertEqual(loaded.info["attachments"], {key: ""})
        self.assertEqual(decode_list(loaded.entries[".itemInfo"])["originKey"], item["key"])
        self.assertEqual(loaded.info["currentPage"], loaded.pages[0]["key"])

    def test_folder_creation_preserves_other_items_and_rejects_collision(self):
        _, item = build_document("Report")
        library = Library(encode_list([{"key": "other", "name": "Other", "document": "other-doc", "extra": 7}]))
        after = place_new(library, item, ["Engineering", "Reviews"])
        self.assertEqual(after.active[0], library.active[0])
        self.assertEqual(after.find(item["key"])[2][-1]["name"], "Reviews")
        self.assertEqual(len(library.active), 1)
        with self.assertRaises(FlexcilError):
            place_new(after, item, [])

    def test_publish_registers_file_then_library_and_retry_is_idempotent(self):
        drive = FakeDrive()
        receipt = prepare_new("test", "Report", folders=["Reports"], probe=True, drive=drive)
        self.assertEqual(drive.uploads, [])
        result = publish_new(receipt["operation_id"], drive)
        self.assertEqual(result["state"], "cloud_verified")
        self.assertFalse(result["device_verified"])
        self.assertEqual(drive.uploads, ["reserved-document-file", "library-file"])
        publish_new(receipt["operation_id"], drive)
        self.assertEqual(len(drive.uploads), 2)
        tree = Library(drive.data["library-file"])
        self.assertEqual(tree.find(receipt["item_id"])[0]["document"], receipt["document_id"])

    def test_concurrent_change_is_preserved(self):
        drive = FakeDrive()
        receipt = prepare_new("test", "Report", probe=True, drive=drive)
        drive.change_during_upload = True
        with self.assertRaises(FlexcilError) as error:
            publish_new(receipt["operation_id"], drive)
        self.assertEqual(error.exception.code, "stale_source")
        self.assertEqual(decode_list(drive.data["library-file"])[0]["key"], "external")
        self.assertEqual(drive.uploads, ["reserved-document-file"])
        self.assertEqual(state.receipt(receipt["operation_id"])["state"], "document_verified")

    def test_modified_candidate_is_never_uploaded(self):
        drive = FakeDrive()
        receipt = prepare_new("test", "Report", probe=True, drive=drive)
        (state.operation_path(receipt["operation_id"]) / "document.flx").write_bytes(b"changed")
        with self.assertRaises(FlexcilError) as error:
            publish_new(receipt["operation_id"], drive)
        self.assertEqual(error.exception.code, "candidate_changed")
        self.assertEqual(drive.uploads, [])
