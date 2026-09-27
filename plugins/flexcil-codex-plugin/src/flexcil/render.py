"""PDF backgrounds and explicit, best-effort Flexcil annotation previews."""
from __future__ import annotations

import io
import math
import os
from pathlib import Path

from .codec import Document, FlexcilError, decode_points, finite_number


def _font(size, supplied=None):
    from PIL import ImageFont
    candidates = [supplied, os.environ.get("FLEXCIL_FONT"),
                  str(Path(__file__).with_name("assets") / "NotoSansKR.ttf"),
                  "/System/Library/Fonts/AppleSDGothicNeo.ttc",
                  "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                  "C:/Windows/Fonts/malgun.ttf", "DejaVuSans.ttf"]
    for candidate in candidates:
        if candidate:
            try:
                return ImageFont.truetype(candidate, size=max(1, round(size)))
            except OSError:
                pass
    raise FlexcilError("font_unavailable", "Set FLEXCIL_FONT or --font to a Unicode TrueType font")


def _rgba(argb, alpha=None):
    if not isinstance(argb, int):
        argb = 0xFF000000
    return ((argb >> 16) & 255, (argb >> 8) & 255, argb & 255, ((argb >> 24) & 255) if alpha is None else alpha)


def render_page(document: Document, page_id: str, scale=2.0, font=None):
    from PIL import Image, ImageDraw
    import pypdfium2 as pdfium
    scale = finite_number(scale, "scale", .1)
    if scale > 8:
        raise FlexcilError("limit_exceeded", "Render scale must not exceed 8")
    page = document.page(page_id)
    w, h = page["frame"]["width"], page["frame"]["height"]
    width, height = math.ceil(w * scale), math.ceil(h * scale)
    if width * height > 40_000_000:
        raise FlexcilError("limit_exceeded", "Page render exceeds 40 megapixels")
    image = Image.new("RGBA", (width, height), "white")
    warnings = []
    attachment = page.get("attachmentPage")
    if attachment:
        try:
            with pdfium.PdfDocument(document.entries["attachment/PDF/" + attachment["file"]]) as pdf:
                if attachment["index"] >= len(pdf):
                    raise FlexcilError("invalid_pdf_page", "Attachment page index exceeds PDF page count")
                pdf_page = pdf[attachment["index"]]
                try:
                    pdf_w, pdf_h = pdf_page.get_size()
                    if max(pdf_w * scale, pdf_h * scale) > 40000 or pdf_w * pdf_h * scale**2 > 40_000_000:
                        raise FlexcilError("limit_exceeded", "PDF render exceeds limits")
                    bitmap = pdf_page.render(scale=scale)
                    try:
                        background = bitmap.to_pil().convert("RGBA")
                    finally:
                        bitmap.close()
                finally:
                    pdf_page.close()
            if background.size != image.size:
                background = background.resize(image.size)
                warnings.append("PDF background resized to Flexcil page frame")
            image.alpha_composite(background)
        except pdfium.PdfiumError as exc:
            raise FlexcilError("invalid_pdf", "Cannot render PDF attachment") from exc
    else:
        warnings.append("No PDF background: template background is not rendered")
    layers = document.objects(page_id)
    by_id = {obj["key"]: (layer, obj) for layer, rows in layers.items() if layer != "objects" for obj in rows}
    ordered, visited = [], set()
    for ref in layers["objects"]:
        if ref["key"] in by_id:
            ordered.append(by_id[ref["key"]])
            visited.add(ref["key"])
        else:
            warnings.append("Unresolved object reference: " + ref["key"])
    ordered.extend(value for key, value in by_id.items() if key not in visited)
    if layers["drawings"]:
        warnings.append("Ink uses observed width-normalized geometry; pen width and pressure are approximate")
    for layer, obj in ordered:
        overlay = Image.new("RGBA", image.size)
        draw = ImageDraw.Draw(overlay)
        if layer == "drawings":
            if obj.get("rotate", 0) != 0 or obj.get("scale", {"x": 1, "y": 1}) != {"x": 1, "y": 1}:
                warnings.append("Transformed ink omitted: " + obj["key"])
                continue
            start = obj.get("start", {})
            sx, sy = finite_number(start.get("x", 0), "stroke.x"), finite_number(start.get("y", 0), "stroke.y")
            points = [((sx + x) * width, (sy + y) * width) for x, y, _ in decode_points(obj["points"])]
            if any(abs(v) > 10_000_000 for point in points for v in point):
                warnings.append("Out-of-range ink omitted: " + obj["key"])
                continue
            if points:
                highlighter = obj.get("mode") == 2
                color = _rgba(obj.get("strokeColor", 0xFF000000), 80 if highlighter else None)
                pen = max(1, round((8 if highlighter else 1.5) * scale))
                if len(points) == 1:
                    x, y = points[0]
                    draw.ellipse((x-pen/2, y-pen/2, x+pen/2, y+pen/2), fill=color)
                else:
                    draw.line(points, fill=color, width=pen, joint="curve")
        elif layer == "texts":
            if obj.get("rotate", 0) != 0:
                warnings.append("Rotated text omitted: " + obj["key"])
                continue
            f = obj.get("frame", {})
            x, y = finite_number(f.get("x", 0), "text.x") * width, finite_number(f.get("y", 0), "text.y") * height
            columns = obj.get("columns", [])
            try:
                style = columns[0]["p"]["span"][0].get("style", {})
            except (IndexError, KeyError, TypeError):
                style = {}
            size = finite_number(style.get("font-size", 18/w), "font-size", 0) * width
            if not 1 <= size <= 2000:
                raise FlexcilError("limit_exceeded", "Text font size exceeds render limits")
            face = _font(size, font)
            text = str(obj.get("text", ""))
            if len(text) > 100000:
                raise FlexcilError("limit_exceeded", "Text exceeds render limit")
            box_width = max(1, finite_number(f.get("width", 1), "text.width", 0) * width)
            lines = []
            for paragraph in text.split("\n"):
                line = ""
                for char in paragraph:
                    if line and draw.textlength(line + char, font=face) > box_width:
                        lines.append(line)
                        line = ""
                    line += char
                lines.append(line)
            draw.multiline_text((x, y), "\n".join(lines), font=face, fill="black", spacing=max(1, int(size*.2)))
            warnings.append("Text uses a substitute font and approximate rich-text layout")
        else:
            warnings.append("Unsupported visible layer omitted: " + layer + "/" + obj["key"])
            continue
        image = Image.alpha_composite(image, overlay)
    rotation = finite_number(page.get("rotate", 0), "page.rotate")
    if rotation:
        warnings.append("Page rotation metadata is not applied; preview uses source coordinates")
    return image.convert("RGB"), {"page_id": page_id, "width": width, "height": height, "fidelity": "approximate" if warnings else "pdf_background", "warnings": list(dict.fromkeys(warnings))}


def export_pdf(document, page_ids=None, scale=2.0, font=None, strict=False):
    """Rasterized annotated export, with a required fidelity report."""
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.lib.utils import ImageReader
    keys = page_ids or [p["key"] for p in document.pages]
    if not keys or len(set(keys)) != len(keys):
        raise FlexcilError("invalid_pages", "Select distinct page UUIDs")
    output, reports = io.BytesIO(), []
    canvas = Canvas(output)
    for key in keys:
        image, report = render_page(document, key, scale, font)
        if strict and report["warnings"]:
            raise FlexcilError("partial_render", "Strict export refuses approximate or omitted content", report=report)
        page = document.page(key)
        size = page["frame"]["width"], page["frame"]["height"]
        canvas.setPageSize(size)
        canvas.drawImage(ImageReader(image), 0, 0, width=size[0], height=size[1])
        canvas.showPage()
        reports.append(report)
        image.close()
    canvas.save()
    return output.getvalue(), {"format": "pdf", "rasterized": True, "pages": reports}
