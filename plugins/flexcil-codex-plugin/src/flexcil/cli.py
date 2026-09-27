"""JSON CLI for Codex. Transport and document authoring stay outside this engine."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from pathlib import Path

from . import __version__
from .codec import Document, FlexcilError, catalog, decode_list, json_bytes, parse_json
from .edits import apply_edits, diff_documents
from .library import Library
from . import state


def document_arg(parser):
    parser.add_argument("input", type=Path)


def parser():
    root = argparse.ArgumentParser(description="Flexcil local engine; all results are JSON")
    root.add_argument("--version", action="version", version=__version__)
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("capabilities")
    sub.add_parser("doctor")
    p = sub.add_parser("import-plan")
    p.add_argument("pdf", type=Path)
    p.add_argument("--title", required=True)
    p.add_argument("--folders", default="[]", help="JSON array of Flexcil folder names")
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--source-url")
    p = sub.add_parser("resolve-import")
    p.add_argument("request", type=Path)
    p.add_argument("document", type=Path)
    p.add_argument("library", type=Path)
    p = sub.add_parser("placement-plan")
    document_arg(p)
    p.add_argument("--item-id", required=True)
    p.add_argument("--folders", required=True, help="JSON array")
    p.add_argument("--title")
    p = sub.add_parser("check-hash")
    document_arg(p)
    p.add_argument("--expected", required=True)
    p = sub.add_parser("setup")
    p.add_argument("--profile", default="default")
    p.add_argument("--folder-id", required=True)
    p.add_argument("--platform", choices=["android", "ios", "unknown"], default="unknown")
    p = sub.add_parser("profile")
    p.add_argument("--profile", default="default")
    for name in ("inspect", "validate", "extract-text"):
        p = sub.add_parser(name)
        document_arg(p)
    p = sub.add_parser("objects")
    document_arg(p)
    p.add_argument("--page", required=True)
    p = sub.add_parser("catalog")
    document_arg(p)
    p.add_argument("--files", type=Path)
    p.add_argument("--include-removed", action="store_true")
    p = sub.add_parser("tree")
    document_arg(p)
    p.add_argument("--trash", type=Path)
    p = sub.add_parser("library-edit")
    document_arg(p)
    p.add_argument("--trash", type=Path)
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p = sub.add_parser("diff")
    p.add_argument("before", type=Path)
    p.add_argument("after", type=Path)
    p = sub.add_parser("edit")
    document_arg(p)
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    for name in ("render", "export-pdf"):
        p = sub.add_parser(name)
        document_arg(p)
        p.add_argument("--page", required=name == "render", action="append" if name == "export-pdf" else "store")
        p.add_argument("--scale", type=float, default=2)
        p.add_argument("--font")
        p.add_argument("--output", type=Path, required=True)
        p.add_argument("--strict", action="store_true")
    p = sub.add_parser("prepare")
    document_arg(p)
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--profile", default="default")
    p.add_argument("--file-id", required=True)
    p.add_argument("--operation-id")
    p.add_argument("--probe", action="store_true")
    for name in ("preflight", "verify", "rollback"):
        p = sub.add_parser(name)
        p.add_argument("operation_id")
        p.add_argument("remote", type=Path)
        if name == "verify":
            p.add_argument("--device-confirmed", action="store_true")
        if name == "rollback":
            p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("receipt")
    p.add_argument("operation_id")
    return root


def capabilities():
    return {"schema": 1, "version": __version__, "document_formats": ["0.0.4", "0.0.5"], "transport": "Codex Google Drive connector", "desktop_flexcil_required": False,
            "local": ["inspect", "extract-text", "objects", "render", "export-pdf", "diff", "edit", "catalog", "tree", "library-edit"],
            "document_edits": ["rename", "add_text", "update_text", "remove_object", "insert_pdf", "add_blank_page", "remove_page", "reorder_pages"],
            "library_edits": ["create_folder", "rename", "move", "trash", "restore"],
            "cloud_updates": "Existing Flexcil-owned files; requires a verified library profile",
            "new_document_registration": "Requires native Flexcil import. Plain Drive upload does not register a sync document.",
            "rendering": "PDF backgrounds, approximate ink and text; unsupported layers reported",
            "concurrency": "Fresh byte comparison and readback; connector does not expose atomic If-Match",
            "permanent_delete": "Not automated; use the native trash UI after reviewing references"}


def run(args):
    cmd = args.command
    if cmd == "check-hash":
        digest = hashlib.sha256(args.input.read_bytes()).hexdigest()
        if digest != args.expected:
            raise FlexcilError("hash_mismatch", "File changed; stop and re-plan", actual_sha256=digest)
        return {"sha256": digest, "matches": True}
    if cmd in ("import-plan", "resolve-import", "placement-plan"):
        from .handoff import prepare_import, resolve_import, placement_plan
        if cmd == "import-plan":
            return prepare_import(args.pdf, args.title, parse_json(args.folders.encode()), args.output_dir, args.source_url)
        if cmd == "resolve-import":
            return resolve_import(args.request, args.document, args.library)
        return placement_plan(Library(args.input.read_bytes()), args.item_id, parse_json(args.folders.encode()), args.title)
    if cmd == "capabilities":
        return capabilities()
    if cmd == "doctor":
        import PIL, pypdfium2, reportlab
        return {"version": __version__, "python": sys.version.split()[0], "pillow": PIL.__version__, "pdfium": str(pypdfium2.PYPDFIUM_INFO), "reportlab": reportlab.Version, "state_directory": str(state.state_root()), "network": "Local engine makes no network calls; use the connected Google Drive app"}
    if cmd == "setup":
        return state.configure(args.profile, args.folder_id, args.platform)
    if cmd == "profile":
        return state.load_profile(args.profile)
    if cmd == "receipt":
        return state.receipt(args.operation_id)
    if cmd == "preflight":
        return state.preflight(args.operation_id, args.remote)
    if cmd == "verify":
        return state.verify(args.operation_id, args.remote, device_confirmed=args.device_confirmed)
    if cmd == "rollback":
        return state.rollback_candidate(args.operation_id, args.remote, args.output)
    if cmd == "prepare":
        return state.prepare(args.input, state.read_json(args.plan), args.profile, args.file_id, args.operation_id, probe=args.probe)
    if cmd == "catalog":
        files = state.read_json(args.files) if args.files else None
        if isinstance(files, dict):
            files = files.get("files", files.get("results", []))
        return catalog(args.input.read_bytes(), files, args.include_removed)
    if cmd in ("tree", "library-edit"):
        library = Library(args.input.read_bytes(), args.trash.read_bytes() if args.trash else None)
        if cmd == "tree":
            return library.tree()
        updated, report = library.edit(state.read_json(args.plan))
        if args.output_dir.exists():
            raise FlexcilError("output_exists", "Choose a new transaction output directory")
        state.private_dir(args.output_dir)
        names = {"active": "documents.list", "trash": ".trash.list"}
        before = {"active": args.input.read_bytes(), "trash": args.trash.read_bytes() if args.trash else None}
        after = updated.bytes()
        report["files"] = []
        for kind in report["upload_order"]:
            name = names[kind]
            state.write_file(args.output_dir / (name + ".before"), before[kind])
            state.write_file(args.output_dir / name, after[kind])
            report["files"].append({"kind": kind, "filename": name, "source_sha256": hashlib.sha256(before[kind]).hexdigest(), "candidate_sha256": hashlib.sha256(after[kind]).hexdigest(), "candidate_path": str((args.output_dir/name).resolve())})
        state.write_file(args.output_dir / "receipt.json", json_bytes(report))
        return report
    if cmd == "diff":
        return diff_documents(Document.load(args.before), Document.load(args.after))
    doc = Document.load(args.input)
    if cmd in ("inspect", "validate"):
        return doc.summary()
    if cmd == "objects":
        return doc.objects(args.page)
    if cmd == "extract-text":
        import pypdfium2 as pdfium
        result = []
        for page in doc.pages:
            pdf_text = ""
            attachment = page.get("attachmentPage")
            if attachment:
                with pdfium.PdfDocument(doc.entries["attachment/PDF/" + attachment["file"]]) as pdf:
                    pdf_page = pdf[attachment["index"]]
                    text_page = pdf_page.get_textpage()
                    try:
                        pdf_text = text_page.get_text_range()
                    finally:
                        text_page.close()
                        pdf_page.close()
            layers = doc.objects(page["key"])
            result.append({"page_id": page["key"], "pdf_text": pdf_text, "text_objects": [o.get("text", "") for o in layers["texts"]], "handwriting": "Use rendered page vision; raw ink is not OCR text", "stroke_count": len(layers["drawings"])})
        return {"pages": result}
    if cmd == "edit":
        updated, report = apply_edits(doc, state.read_json(args.plan))
        data = updated.to_bytes()
        state.write_file(args.output, data)
        report.update(output=str(args.output.resolve()), sha256=hashlib.sha256(data).hexdigest())
        return report
    from .render import render_page, export_pdf
    if cmd == "render":
        image, report = render_page(doc, args.page, args.scale, args.font)
        if args.strict and report["warnings"]:
            raise FlexcilError("partial_render", "Strict rendering refuses approximate content", report=report)
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        data = stream.getvalue()
    elif cmd == "export-pdf":
        data, report = export_pdf(doc, args.page, args.scale, args.font, args.strict)
    else:
        raise FlexcilError("unknown_command", "Unknown command")
    state.write_file(args.output, data)
    return {**report, "output": str(args.output.resolve())}


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = run(args)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, allow_nan=False))
        return 0
    except FlexcilError as exc:
        print(json.dumps({"ok": False, "error": {"code": exc.code, "message": str(exc), **exc.details}}, ensure_ascii=False))
        return 2
    except ModuleNotFoundError as exc:
        print(json.dumps({"ok": False, "error": {"code": "runtime_missing", "message": "Run flexcil-setup with the bundled runtime or bootstrap an isolated environment", "module": exc.name}}))
        return 2
    except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
        print(json.dumps({"ok": False, "error": {"code": "invalid_input", "message": str(exc)}}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
