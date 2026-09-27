"""Direct Google transport. OAuth state and tokens never enter plugin source."""
from __future__ import annotations

import base64
import hashlib
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .codec import FlexcilError, LIMITS, json_bytes, parse_json
from . import state

API = "https://www.googleapis.com/drive/v3/"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/"
TOKEN = "https://oauth2.googleapis.com/token"
AUTHORIZE = "https://accounts.google.com/o/oauth2/auth"
SCOPE = "https://www.googleapis.com/auth/drive.file https://www.googleapis.com/auth/userinfo.email"
MIME = "application/com.flexcil.object"


def private_path(profile, name):
    state.profile_path(profile)  # Validate profile, independently of current working directory.
    return state.state_root() / "connections" / profile / name


def save(profile, name, value):
    path = private_path(profile, name)
    state.private_dir(path.parent)
    state.write_file(path, json_bytes(value), replace=True)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward OAuth/Drive credentials to a redirect target.


def http(method, url, data=None, headers=None, limit=LIMITS.archive_bytes):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {"www.googleapis.com", "oauth2.googleapis.com"} or parsed.port not in (None, 443) or parsed.username or parsed.password:
        raise FlexcilError("untrusted_endpoint", "Only Google HTTPS API endpoints are accepted")
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        response = urllib.request.build_opener(NoRedirect).open(request, timeout=45)
    except urllib.error.HTTPError as exc:
        response = exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # Do not expose request URLs, tokens, or provider response bodies.
        raise FlexcilError("network_error", "Google request interrupted; inspect operation receipt before retrying") from exc
    with response:
        body = response.read(limit + 1)
        if len(body) > limit:
            raise FlexcilError("limit_exceeded", "Google response exceeds the configured size limit")
        return response.code, dict(response.headers.items()), body


def checked_json(status, body):
    if not 200 <= status < 300:
        raise FlexcilError("google_http_error", "Google rejected the request", status=status)
    value = parse_json(body, "Google response") if body else {}
    if not isinstance(value, dict):
        raise FlexcilError("invalid_response", "Google response must be an object")
    return value


def auth_start(profile, config_path):
    """Use an explicitly configured client; do not silently impersonate another app."""
    state.load_profile(profile)
    config = state.read_json(config_path)
    client = config.get("client_id", "")
    redirect = config.get("redirect_uri", "")
    if not isinstance(client, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+\.apps\.googleusercontent\.com", client):
        raise FlexcilError("invalid_oauth_client", "Configure a Google public OAuth client ID")
    if not isinstance(redirect, str) or any(c in redirect for c in "\r\n"):
        raise FlexcilError("invalid_redirect", "Invalid OAuth redirect URI")
    parts = urllib.parse.urlsplit(redirect)
    if not parts.scheme or parts.query or parts.fragment or parts.username or parts.password:
        raise FlexcilError("invalid_redirect", "Use the registered callback URI without query or fragment")
    if parts.scheme in ("http", "https") and parts.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise FlexcilError("invalid_redirect", "Web callbacks must use a registered local loopback URI")
    if parts.scheme in ("file", "javascript", "data"):
        raise FlexcilError("invalid_redirect", "Unsupported OAuth callback scheme")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    pending = {"schema": 1, "client_id": client, "redirect_uri": redirect, "verifier": verifier,
               "state": secrets.token_urlsafe(32), "created_at": time.time(), "scope": SCOPE}
    save(profile, "pending.json", pending)
    parameters = {"client_id": client, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
                  "state": pending["state"], "code_challenge": challenge, "code_challenge_method": "S256", "access_type": "offline"}
    return {"profile": profile, "authorization_url": AUTHORIZE + "?" + urllib.parse.urlencode(parameters),
            "expires_in_seconds": 900, "status": "awaiting_user_login",
            "next": "Complete Google consent; save this flow's callback URL locally and use connect-finish. Do not paste tokens into chat.",
            "registration": "Not enabled until probe verifies existing Flexcil appProperties under this connection"}


def auth_finish(profile, callback_path):
    pending = state.read_json(private_path(profile, "pending.json"))
    if not 0 <= time.time() - pending["created_at"] <= 900:
        raise FlexcilError("expired_login", "Start a fresh login; this request expired")
    with Path(callback_path).open("rb") as stream:
        callback_bytes = stream.read(16385)
    if len(callback_bytes) > 16384:
        raise FlexcilError("invalid_callback", "OAuth callback is too long")
    try:
        callback = callback_bytes.decode("utf-8").strip()
    except UnicodeError as exc:
        raise FlexcilError("invalid_callback", "OAuth callback must be UTF-8") from exc
    parsed, expected = urllib.parse.urlsplit(callback), urllib.parse.urlsplit(pending["redirect_uri"])
    if (parsed.scheme, parsed.netloc, parsed.path) != (expected.scheme, expected.netloc, expected.path) or parsed.fragment:
        raise FlexcilError("invalid_callback", "OAuth callback does not match this flow's redirect URI")
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    if len(query.get("state", [])) != 1 or not secrets.compare_digest(query["state"][0], pending["state"]):
        raise FlexcilError("invalid_state", "OAuth state mismatch; no credentials were exchanged")
    if "error" in query:
        raise FlexcilError("login_declined", "Google authorization did not complete")
    if len(query.get("code", [])) != 1 or not query["code"][0]:
        raise FlexcilError("invalid_callback", "Callback needs exactly one authorization code")
    form = {"client_id": pending["client_id"], "redirect_uri": pending["redirect_uri"],
            "grant_type": "authorization_code", "code": query["code"][0], "code_verifier": pending["verifier"]}
    status, _, body = http("POST", TOKEN, urllib.parse.urlencode(form).encode(), {"Content-Type": "application/x-www-form-urlencoded"}, 1024 * 1024)
    token = checked_json(status, body)
    _save_token(profile, pending["client_id"], token)
    private_path(profile, "pending.json").unlink()
    return {"profile": profile, "status": "authenticated", "registration": "unverified; run connection-probe"}


def _save_token(profile, client_id, token, old=None):
    if not isinstance(token.get("access_token"), str) or token.get("token_type", "").lower() != "bearer":
        raise FlexcilError("invalid_token_response", "Google did not return a Bearer token")
    lifetime = token.get("expires_in")
    if type(lifetime) not in (int, float) or not 0 < lifetime <= 86400:
        raise FlexcilError("invalid_token_response", "Invalid token expiration")
    value = {"client_id": client_id, "access_token": token["access_token"], "expires_at": time.time() + lifetime,
             "refresh_token": token.get("refresh_token") or (old or {}).get("refresh_token"), "scope": token.get("scope", "")}
    save(profile, "credentials.json", value)
    return value


def access_token(profile):
    value = state.read_json(private_path(profile, "credentials.json"))
    if value["expires_at"] > time.time() + 60:
        return value["access_token"]
    if not value.get("refresh_token"):
        raise FlexcilError("login_required", "Google session expired; reconnect this profile")
    form = {"client_id": value["client_id"], "refresh_token": value["refresh_token"], "grant_type": "refresh_token"}
    status, _, body = http("POST", TOKEN, urllib.parse.urlencode(form).encode(), {"Content-Type": "application/x-www-form-urlencoded"}, 1024 * 1024)
    token = checked_json(status, body)
    return _save_token(profile, value["client_id"], token, value)["access_token"]


def file_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{5,200}", value):
        raise FlexcilError("invalid_file_id", "Use a raw Google Drive file ID")
    return value


class Drive:
    def __init__(self, profile):
        self.profile = profile

    def request(self, method, url, data=None, headers=None, *, raw=False, statuses=()):
        headers = {**(headers or {}), "Authorization": "Bearer " + access_token(self.profile)}
        status, response_headers, body = http(method, url, data, headers)
        if status in statuses:
            return status, response_headers, body
        if not 200 <= status < 300:
            raise FlexcilError("google_http_error", "Google rejected the Drive request", status=status)
        return body if raw else checked_json(status, body)

    def metadata(self, key):
        fields = "id,name,mimeType,parents,trashed,appProperties,modifiedTime,version,md5Checksum,size"
        return self.request("GET", API + "files/" + file_id(key) + "?" + urllib.parse.urlencode({"fields": fields}))

    def download(self, key):
        return self.request("GET", API + "files/" + file_id(key) + "?alt=media", raw=True)

    def list(self, folder):
        query = "'" + file_id(folder) + "' in parents and trashed = false and appProperties has {key = 'Flexcil' and value = 'MakeFromFlexcil'}"
        result, page = [], None
        for _ in range(1000):
            params = {"q": query, "pageSize": 1000, "fields": "nextPageToken,files(id,name,mimeType,appProperties,modifiedTime,version,size)"}
            if page:
                params["pageToken"] = page
            response = self.request("GET", API + "files?" + urllib.parse.urlencode(params))
            result.extend(response.get("files", []))
            page = response.get("nextPageToken")
            if not page:
                return result
        raise FlexcilError("limit_exceeded", "Drive listing exceeded pagination limit")

    def probe(self):
        profile = state.load_profile(self.profile)
        root = self.metadata(profile["library_folder_id"])
        props = root.get("appProperties", {})
        if root.get("trashed") or root.get("mimeType") != "application/vnd.google-apps.folder" or props.get("Flexcil") != "MakeFromFlexcil" or props.get("SyncId") != "Flexcil":
            raise FlexcilError("wrong_oauth_context", "This connection cannot see Flexcil's private registration properties. A separate client ID does not solve this.")
        files = self.list(profile["library_folder_id"])
        lists = [f for f in files if f.get("name") == "documents.list" and f.get("appProperties", {}).get("SyncId") == "documents.list"]
        if len(lists) != 1:
            raise FlexcilError("ambiguous_library", "Expected one native documents.list under the sync root")
        return {"profile": self.profile, "status": "private_properties_visible", "library_file_id": lists[0]["id"], "file_count": len(files)}

    def generate_id(self):
        return file_id(self.request("GET", API + "files/generateIds?count=1&space=drive")["ids"][0])

    def upload(self, data, metadata, key=None, *, session_path=None):
        """Resumable POST/PATCH with persistent session, including uncertain-response recovery."""
        if not data or len(data) > LIMITS.archive_bytes:
            raise FlexcilError("limit_exceeded", "Upload must contain 1–512 MiB")
        digest = hashlib.sha256(data).hexdigest()
        session = None
        if session_path and Path(session_path).exists():
            saved = state.read_json(session_path)
            if saved["sha256"] != digest or saved["metadata"] != metadata or saved["file_id"] != key:
                raise FlexcilError("upload_changed", "Resumable upload bytes or metadata changed")
            session = upload_session(saved["url"])
        if session is None:
            path = "files/" + file_id(key) if key else "files"
            url = UPLOAD + path + "?uploadType=resumable&fields=id,name,appProperties,md5Checksum"
            status, headers, _ = self.request("PATCH" if key else "POST", url, json_bytes(metadata),
                {"Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Type": MIME, "X-Upload-Content-Length": str(len(data))}, statuses=(200, 201))
            session = next((v for k, v in headers.items() if k.lower() == "location"), None)
            session = upload_session(session)
            if session_path:
                state.write_file(session_path, json_bytes({"url": session, "sha256": digest, "file_id": key, "metadata": metadata}), replace=True)
            start = 0
        else:
            status, headers, body = self.request("PUT", session, b"", {"Content-Range": "bytes */" + str(len(data)), "Content-Length": "0"}, statuses=(200, 201, 308, 404))
            if status in (200, 201):
                return checked_json(status, body)
            if status == 404:
                raise FlexcilError("upload_session_expired", "Upload session expired; verify the reserved Drive ID before restarting")
            start = _next_byte(headers)
        while start < len(data):
            end = min(start + 4 * 1024 * 1024, len(data))
            status, headers, body = self.request("PUT", session, data[start:end],
                {"Content-Type": MIME, "Content-Length": str(end-start), "Content-Range": f"bytes {start}-{end-1}/{len(data)}"}, statuses=(200, 201, 308))
            if status in (200, 201):
                return checked_json(status, body)
            received = _next_byte(headers)
            if not start < received <= end:
                raise FlexcilError("invalid_upload_range", "Google returned an unexpected received-byte range")
            start = received
        raise FlexcilError("upload_incomplete", "Google has not confirmed upload completion")


def upload_session(value):
    parts = urllib.parse.urlsplit(value or "")
    if (parts.scheme != "https" or parts.netloc != "www.googleapis.com" or parts.fragment
            or not re.fullmatch(r"/upload/drive/v3/files(?:/[A-Za-z0-9_-]+)?", parts.path)):
        raise FlexcilError("invalid_upload_session", "Unexpected Google upload session URL")
    return value


def _next_byte(headers):
    value = next((v for k, v in headers.items() if k.lower() == "range"), "")
    if not value:
        return 0
    match = re.fullmatch(r"bytes=0-(\d+)", value)
    if not match:
        raise FlexcilError("invalid_upload_range", "Invalid Google received-byte range")
    return int(match[1]) + 1
