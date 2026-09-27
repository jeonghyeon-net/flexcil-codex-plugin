"""Standard PDF handoff between document-producing skills and Flexcil."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .codec import Document, FlexcilError, json_bytes
from .edits import identifier
from .library import Library
from .state import private_dir, read_json, write_file


def prepare_import(pdf_path, title, folder_path, output_directory, source_url=None):
    """Prepare an honest pending-import job; never report a Drive upload as registration."""
    import pypdfium2 as pdfium
    title = Library.title(title)
    if not isinstance(folder_path, list) or len(folder_path) > 100:
        raise FlexcilError("invalid_folder_path", "Folder path must be an array of at most 100 names")
    for name in folder_path:
        Library.title(name)
    pdf_path = Path(pdf_path)
    if pdf_path.stat().st_size > 256 * 1024 * 1024:
        raise FlexcilError("limit_exceeded", "PDF exceeds 256 MB")
    data = pdf_path.read_bytes()
    try:
        with pdfium.PdfDocument(data) as pdf:
            count = len(pdf)
            if count < 1:
                raise FlexcilError("invalid_pdf", "PDF must contain a page")
    except pdfium.PdfiumError as exc:
        raise FlexcilError("invalid_pdf", "Cannot read the PDF artifact") from exc
    directory = Path(output_directory)
    if directory.exists():
        raise FlexcilError("output_exists", "Import job output directory already exists")
    job_id = identifier()
    private_dir(directory)
    filename = "Flexcil-import-" + job_id + ".pdf"
    write_file(directory / filename, data)
    request = {"schema": 1, "job_id": job_id, "state": "awaiting_native_import", "title": title, "folder_path": folder_path, "pdf_sha256": hashlib.sha256(data).hexdigest(), "page_count": count, "artifact_path": str((directory / filename).resolve()), "source_url": source_url, "next_action": "Import the PDF with Flexcil, then sync. Registration cannot be completed by generic Drive upload."}
    write_file(directory / "request.json", json_bytes(request))
    return request


def resolve_import(request_path, document_path, library_path):
    request = read_json(request_path)
    doc = Document.load(document_path)
    expected = request["pdf_sha256"]
    matches = [key for key, data in doc.entries.items() if key.startswith("attachment/PDF/") and hashlib.sha256(data).hexdigest() == expected]
    if not matches:
        raise FlexcilError("import_not_matched", "Document does not contain the exact PDF from this job")
    library = Library(Path(library_path).read_bytes())
    nodes = [n for n, _, _ in library._walk(library.active) if n.get("document", "").upper() == doc.info["key"].upper() and n.get("state") != "removed"]
    if len(nodes) != 1:
        raise FlexcilError("import_not_registered", "Imported document is not uniquely registered in the active library")
    return {"schema": 1, "job_id": request["job_id"], "state": "registered_needs_placement", "document_id": doc.info["key"], "item_id": nodes[0]["key"], "document_sha256": doc.original_sha256, "library_plan": placement_plan(library, nodes[0]["key"], request["folder_path"], request["title"]), "document_plan": {"schema": 1, "expected_sha256": doc.original_sha256, "operations": [{"op": "rename", "title": request["title"]}]}}


def placement_plan(library, item_id, folder_path, title=None):
    library.find(item_id)
    if not isinstance(folder_path, list) or len(folder_path) > 100:
        raise FlexcilError("invalid_folder_path", "Folder path must be an array of at most 100 names")
    operations, nodes, parent = [], library.active, "root"
    for name in folder_path:
        Library.title(name)
        matches = [n for n in nodes if n.get("name") == name and n.get("state") != "removed"]
        if len(matches) > 1 or (matches and matches[0].get("type") != 1):
            raise FlexcilError("ambiguous_folder", "Folder path is ambiguous or occupied by a document", name=name)
        if matches:
            parent, nodes = matches[0]["key"], matches[0].get("children", [])
        else:
            key = identifier()
            operations.append({"op": "create_folder", "item_id": key, "title": name, "parent_id": parent})
            parent, nodes = key, []
    operations.append({"op": "move", "item_id": item_id, "parent_id": parent})
    if title is not None:
        operations.append({"op": "rename", "item_id": item_id, "title": Library.title(title)})
    return {"schema": 1, "expected_sha256": library.source_sha256, "operations": operations}
