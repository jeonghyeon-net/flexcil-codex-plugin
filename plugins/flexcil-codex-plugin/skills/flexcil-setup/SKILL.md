---
name: flexcil-setup
description: Configure or diagnose a user's Flexcil library connection, Python runtime, and per-library sync verification without putting personal identifiers or credentials in the public plugin. Use when connecting Flexcil for the first time, changing libraries, or resolving missing-runtime and sync-compatibility errors.
---

# Flexcil setup

Keep all per-user information outside the plugin installation. Never edit the plugin manifest with account IDs, emails, folder IDs, document IDs, local account paths, or tokens. Google authentication belongs to the connected Google Drive app; do not request bearer tokens.

1. Locate this plugin's `scripts/flexcil.py`. Prefer the Codex bundled Python from `load_workspace_dependencies`; otherwise use an available Python 3.10+. Run `doctor`.
2. If dependencies are missing, run `scripts/bootstrap.py --directory <private-runtime-directory>` with Python 3.10+. This creates an isolated environment. Use the returned interpreter for subsequent commands. Do not install into system Python. Do not write a virtual environment into the installed plugin.
3. Connect Google Drive through the normal Codex connection UI if needed. Discover the sync folder by metadata and inspect its children with folder `fetch` or `list_folder`. General search can omit Flexcil's custom MIME files. Do not accept an empty search as an empty library.
4. Identify `documents.list`, `.trash.list`, and the document files. If multiple candidate sync roots exist, ask which library the user wants. Do not require a fixed set of chosen notes: configure a library root, and resolve notes at use time.
5. Run `setup --profile <local-name> --folder-id <verified-id> --platform android|ios|unknown`. Show where settings are stored. Default storage is the platform's per-user application state directory; `FLEXCIL_STATE_DIR` overrides it. Profiles store library IDs and compatibility status, not credentials.
6. Before regular cloud writes, use a dedicated test note created by the user. Download it, create a text plan, and run `prepare --probe`. Follow the normal preflight/upload/readback procedure. Ask the user to confirm the text appears on their device, add a small mark, close the note, and sync.
7. Fetch again and run `verify <operation-id> <readback> --device-confirmed` only after that explicit confirmation. The engine requires preserved edits plus an additional device object. This records compatibility for that library. Never set `sync_verified` manually or infer device verification from an HTTP success.

Repeat the probe after a platform or relevant Flexcil update changes behavior. Do not claim a precise app version from “latest”; record the version as unknown unless actually observed.

New-document cloud registration is a separate capability from editing existing files. The current plugin prepares PDFs for native import and completes placement after import. Do not promise unattended new-document creation or install device-debugging tools to conceal this limitation.
