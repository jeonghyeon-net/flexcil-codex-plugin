# Google Drive transport

Use the direct connection when configured; otherwise use actual connector schemas. The plugin ships no client configuration, token, proxy, or remote service.

## Direct connection

`connection-probe` checks the native root and library properties. `cloud-tree` returns fresh active/trash lists, item IDs, document-to-file mappings, and plan hashes. `cloud-list` paginates the native root; `cloud-download` saves a selected file to a new local path.

- New document: `create-document` builds a prepared operation, then `publish-document` registers the reserved Drive ID and updates the virtual library. Both raw files and native properties are read back. An initial test uses `--probe` and `verify-new-document --device-confirmed` after the user actually adds a mark.
- Existing document: download/read the current file, build a matching edit plan, run `cloud-prepare-edit`, then `publish-update`.
- Folders, moves, names, trash and restore: use a fresh `cloud-tree` result to build a library plan, run `cloud-prepare-library`, then `publish-update`. Document rename automatically updates both the archive title and library title.

Operations keep source/candidate hashes and stable upload metadata. Retrying the same operation skips confirmed steps and resumes its upload session. If another device changed any source, preserve partial results and re-plan. There is still a final read/write race; do not claim atomic transactions.

The remainder describes the connector fallback for existing files.

## Discover

Search for a sync-root folder, then call `google_drive_fetch` on its observed folder URL or `google_drive_list_folder`. Folder `fetch` returns at most 100 children. The generic `search` action's `document` filter can exclude `application/com.flexcil.object`; this was observed during integration testing.

Do not call a partial listing complete. If a folder hits the provider limit, inspect `list_folder` capacity and metadata. Use a paginated unfiltered provider listing when exposed; otherwise report incomplete discovery and accept exact file IDs/URLs from the user. Never invent a pagination parameter unsupported by the connector.

Map actual Drive `.flx` filenames to `documents.list` document UUIDs. Do not infer the title from a UUID filename or include trash/unreferenced files in an active-library listing.

## Download

Read/reuse metadata to confirm MIME type and identity. For stored files use `google_drive_fetch(url=observed_url, download_raw_file=true, include_base64=false)`. Materialize the returned authenticated file reference in a private working directory. Do not print temporary download URLs or ask for inline base64. Do not use Google Workspace `export_file` on `.flx` or `.list`.

The connector may normalize metadata and omit requested `version`, `md5Checksum`, or `appProperties`. Missing fields are unavailable, not proof of absence. The engine therefore uses fresh downloaded bytes as its preflight evidence.

## Update an existing document

1. `prepare <source.flx> --plan <plan.json> --profile <profile> --file-id <verified-id>` saves original bytes, candidate, and receipt. The document UUID must match the selected library entry. Compare Drive parents with the configured root before preparation.
2. Download the same remote file immediately before upload. Run `preflight <operation-id> <fresh.flx>`. If the candidate already exists remotely, skip upload and resume verification.
3. Call `google_drive_update_file` with the verified **same file ID**, candidate's absolute path as `file_uri`, and the original MIME type. Omit `name`, `addParents`, and `removeParents` for content-only updates. Preserve Flexcil's existing file identity and app properties by not changing metadata.
4. Fetch the updated bytes. Run `verify <operation-id> <readback.flx>`.
5. For the setup probe, separately obtain actual device confirmation and a subsequent tablet edit before `--device-confirmed`.

The connector has no atomic conditional-write parameter. A preflight cannot eliminate the race between the final download and upload. Avoid simultaneous editing of the target while writing; if a conflict is detected, preserve copies and re-plan. Do not describe this as compare-and-swap.

## Update library/trash lists

`library-edit` saves before/candidate pairs, expected hashes, and `upload_order`. Bind each logical list to its verified existing Drive ID. Freshly download **all affected lists** and `check-hash` against source hashes before starting. Before each individual upload, refresh that file again. Follow the emitted order; verify each file's readback hash before continuing.

- Trash: write the recoverable trash record, then remove the active reference.
- Restore: write the active reference, then remove the trash record.

If interrupted, compare each remote file with its before and candidate hashes. Candidate means that step already succeeded; before means it still needs work; neither means external changes require re-planning. Retain the receipt until all steps are verified. Duplicate references during an interrupted transaction must not cause automatic file deletion.

## Recovery

`rollback` only prepares restoration if fresh remote bytes exactly match that operation's candidate. It does not upload. Freshly preflight again before writing it. If someone edited the document afterward, recover through a new reviewed plan, not by overwriting with an old backup.

Private `appProperties` belong to the creating app. The Android code uses its Flexcil marker to enumerate sync files. The direct connection has demonstrated visibility and creation under the explicitly configured, user-consented OAuth context. The generic connector does not expose equivalent native registration; never infer it from access to the same user's Drive.
