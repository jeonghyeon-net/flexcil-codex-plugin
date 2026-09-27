# Command contract

Run `python <plugin>/scripts/flexcil.py --help` for exact arguments. JSON stdout is `{"ok":true,"result":...}` or `{"ok":false,"error":{"code":...,"message":...}}`. Error exit status is 2. New output paths must not already exist.

| Task | Command |
|---|---|
| Runtime/features | `doctor`, `capabilities` |
| Library settings | `setup --folder-id ID --profile NAME --platform android`, `profile` |
| Public connection settings from supplied package | `connection-config --apk app.apk --profile NAME` |
| System-browser PKCE login | `connect --profile NAME`, `connect-finish --callback-file PATH --profile NAME` |
| Native property access | `connection-probe --profile NAME` |
| Fresh cloud library and file mapping | `cloud-tree --profile NAME`, `cloud-list --profile NAME` |
| Direct download | `cloud-download --profile NAME --file-id ID --output local.flx` |
| New native document | `create-document --profile NAME --title TITLE --pdf report.pdf --folders '["Engineering","Reviews"]'` |
| Register prepared new document | `publish-document OPERATION_ID` |
| Initial new-document device round trip | `verify-new-document OPERATION_ID --device-confirmed` |
| Prepare direct existing-document edit | `cloud-prepare-edit --profile NAME --file-id ID --plan edits.json` |
| Prepare direct folder/name/move/trash/restore | `cloud-prepare-library --profile NAME --plan library.json` |
| Publish/resume a direct update | `publish-update OPERATION_ID` |
| Virtual folders and trash | `tree documents.list --trash .trash.list` |
| Join documents to Drive metadata | `catalog documents.list --files metadata.json` |
| Page IDs, dimensions, native text | `inspect document.flx` |
| PDF text and native text | `extract-text document.flx` |
| Raw object layers | `objects document.flx --page UUID` |
| Preview | `render document.flx --page UUID --output page.png` |
| Rasterized annotated PDF | `export-pdf document.flx --output export.pdf` |
| Semantic comparison | `diff before.flx after.flx` |
| Offline edit | `edit document.flx --plan edits.json --output edited.flx` |
| Cloud write preparation | `prepare document.flx --plan edits.json --file-id ID --profile NAME` |
| Resume and verify | `receipt UUID`, `preflight UUID fresh.flx`, `verify UUID readback.flx` |
| Library mutation | `library-edit documents.list --trash .trash.list --plan edits.json --output-dir transaction` |
| Check a list/readback | `check-hash file --expected SHA256` |
| Plan folder path | `placement-plan documents.list --item-id UUID --folders '["Engineering","Reviews"]'` |
| Another skill's PDF | `import-plan report.pdf --title 'PR report' --folders '["Engineering","Reviews"]' --output-dir job` |
| Match imported artifact | `resolve-import job/request.json synced.flx documents.list` |

`render` and `export-pdf` accept `--scale`, `--font`, and `--strict`. `--strict` refuses any approximate/omitted annotation. PDF export is rasterized; it is not an editable vector export.

## Document edit plan

```json
{
  "schema": 1,
  "expected_sha256": "HASH_FROM_INSPECT",
  "operations": [
    {
      "op": "add_text",
      "page_id": "PAGE_UUID_FROM_INSPECT",
      "text": "Summary from Codex",
      "frame": {"x": 40, "y": 100, "width": 480, "height": 90},
      "font_size": 18
    }
  ]
}
```

Coordinates and font sizes are in page points. All IDs refer to observed source objects, except new optional IDs, which must be new UUIDs. The engine generates new UUIDs when omitted and records them in the receipt.

- `add_text`: `page_id`, `text`, `frame`; optional `object_id`, `font_size`.
- `update_text`: `page_id`, `object_id`, `text`; optional `frame`, `font_size`. Replaces rich text with a single text span while retaining the original first span style.
- `remove_object`: `page_id`, `object_id`; removes its layer object and index reference.
- `insert_pdf`: absolute `pdf` path; optional one-based `pages`, `after` (page UUID, `start`, or omitted for append), `attachment_id`, `page_ids`. PDF content stays in its original attachment; existing ink is untouched.
- `add_blank_page`: optional `width`, `height` in points, `after`, `page_ids`, `attachment_id`.
- `reorder_pages`: `page_ids` containing every existing page exactly once.
- `remove_page`: `page_id`. Keeps orphaned binary assets to preserve unknown references. Refuses removal of the last page.
- `rename`: `title`. Pair with a library rename when applying to a cloud library.

## Library edit plan

Use `schema: 1`, `expected_sha256` from `tree`, and `operations`. Include `expected_trash_sha256` for trash/restore. Use list-item IDs, not document or Drive IDs.

- `create_folder`: `title`, optional `parent_id` (default `root`) and new `item_id`.
- `move`: `item_id`, optional `parent_id` (default `root`). Rejects folder cycles.
- `rename`: `item_id`, `title`.
- `trash`: `item_id`. A folder operation includes its subtree.
- `restore`: top-level trash `item_id`, optional `parent_id` (default `root`).

Use `placement-plan` when the user gives a path; it reuses existing folders and emits only required new folders. Never collapse similarly named folders across different parents.
