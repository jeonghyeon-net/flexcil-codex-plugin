---
name: flexcil-setup
description: Configure or diagnose a user's Flexcil library connection, Python runtime, and per-library sync verification without putting personal identifiers or credentials in the public plugin. Use when connecting Flexcil for the first time, changing libraries, or resolving missing-runtime and sync-compatibility errors.
---

# Flexcil setup

Keep all per-user information outside the plugin installation. Never edit the manifest with account IDs, emails, folder IDs, document IDs, local paths, client configuration, or tokens. Do not request bearer tokens or copy credentials from another app. Direct login uses a fresh user-consented PKCE flow.

1. Locate this plugin's `scripts/flexcil.py`. Prefer the Codex bundled Python from `load_workspace_dependencies`; otherwise use an available Python 3.10+. Run `doctor`.
2. If dependencies are missing, run `scripts/bootstrap.py --directory <private-runtime-directory>` with Python 3.10+. This creates an isolated environment. Use the returned interpreter for subsequent commands. Do not install into system Python. Do not write a virtual environment into the installed plugin.
3. Connect Google Drive through the normal Codex connection UI if needed. Discover the sync folder by metadata and inspect its children with folder `fetch` or `list_folder`. General search can omit Flexcil's custom MIME files. Do not accept an empty search as an empty library.
4. Identify `documents.list`, `.trash.list`, and the document files. If multiple candidate sync roots exist, ask which library the user wants. Do not require a fixed set of chosen notes: configure a library root, and resolve notes at use time.
5. Run `setup --profile <local-name> --folder-id <verified-id> --platform android|ios|unknown`. Show where settings are stored. Default storage is the platform's per-user application state directory; `FLEXCIL_STATE_DIR` overrides it. Profiles store library IDs and compatibility status, not credentials.
6. For direct registration, use an explicitly provided local OAuth client configuration, or read public configuration from a user-selected Flexcil APK/XAPK with `connection-config --profile <name> --apk <path>`. This does not run/install the APK or extract user tokens. Record the package's origin/version/checksum; do not imply its signature was verified. If the caller has not supplied a package or trusted source, ask for it. The public plugin must not ship the APK or its extracted client configuration.
7. Run `connect --profile <name>` (or pass `--client-config <path>`). On macOS this builds a local callback receiver using Swift tools; it refuses to replace another installed app's callback handler. Open the returned URL in the system browser. Explain that Google displays the configured app's identity, while this plugin receives/uses the new connection. Have the user complete account selection and consent. The receiver writes the one-time callback privately; use `connect-finish --callback-file <returned-path>`. Never ask the user to paste a callback/code/token into chat. Windows/Linux automatic callbacks are not implemented; a configured external receiver is required.
8. Run `connection-probe`. It must actually read native Flexcil properties on the root and list. An arbitrary separate OAuth project does not satisfy this requirement. Do not bypass the check or advertise the generic Codex connector as providing these private properties.
9. Before regular writes, create a dedicated new test document using `create-document --probe`, then `publish-document`. Ask the user to confirm the PDF appears, add a small mark, close, and sync. Run `verify-new-document --device-confirmed` only after explicit confirmation. This requires preserved content and a new device object, and records compatibility. Never set verification flags manually.
10. An existing-note probe remains available with `prepare --probe` and `verify --device-confirmed`, using the connector procedure. Treat it separately from new-document registration support.

Repeat the probe after a platform or relevant Flexcil update changes behavior. Do not claim a precise app version from “latest”; record the version as unknown unless actually observed.

New PDF document registration has passed a real Android round trip through the direct connection. Native import is a fallback, not the normal direct workflow. Do not claim that this establishes every Flexcil feature, every app version, or other platforms.
