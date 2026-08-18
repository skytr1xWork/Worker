import csv
import io
import json
import os
import re
from docx import Document
import markdown
from PIL import Image

SUPPORTED_IMAGE_FORMATS = {
    "PNG": {"ext": "png", "mime": "image/png"},
    "JPG": {"ext": "jpg", "mime": "image/jpeg"},
    "WEBP": {"ext": "webp", "mime": "image/webp"},
    "BMP": {"ext": "bmp", "mime": "image/bmp"},
    "TIFF": {"ext": "tiff", "mime": "image/tiff"},
    "ICO": {"ext": "ico", "mime": "image/x-icon"},
    "PDF": {"ext": "pdf", "mime": "application/pdf"},
    "GIF": {"ext": "gif", "mime": "image/gif"},
}

IMAGE_EXTENSIONS = {
    "png": "PNG",
    "jpg": "JPG",
    "jpeg": "JPG",
    "webp": "WEBP",
    "bmp": "BMP",
    "tiff": "TIFF",
    "tif": "TIFF",
    "ico": "ICO",
    "gif": "GIF",
    "pdf": "PDF",
}

IMAGE_MIME_TYPES = {
    "image/png": "PNG",
    "image/jpeg": "JPG",
    "image/pjpeg": "JPG",
    "image/webp": "WEBP",
    "image/bmp": "BMP",
    "image/x-ms-bmp": "BMP",
    "image/tiff": "TIFF",
    "image/x-icon": "ICO",
    "image/vnd.microsoft.icon": "ICO",
    "image/gif": "GIF",
}

SUPPORTED_DOCUMENT_FORMATS = {
    "DOCX": {"ext": "docx", "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    "MD": {"ext": "md", "mime": "text/markdown"},
    "TXT": {"ext": "txt", "mime": "text/plain"},
    "DAT": {"ext": "dat", "mime": "application/octet-stream"},
    "CSV": {"ext": "csv", "mime": "text/csv"},
    "TSV": {"ext": "tsv", "mime": "text/tab-separated-values"},
    "JSON": {"ext": "json", "mime": "application/json"},
    "XML": {"ext": "xml", "mime": "application/xml"},
    "LOG": {"ext": "log", "mime": "text/plain"},
    "HTML": {"ext": "html", "mime": "text/html"},
}

DOCUMENT_EXTENSIONS = {
    "docx": "DOCX",
    "md": "MD",
    "markdown": "MD",
    "txt": "TXT",
    "dat": "DAT",
    "csv": "CSV",
    "tsv": "TSV",
    "json": "JSON",
    "xml": "XML",
    "log": "LOG",
    "html": "HTML",
    "htm": "HTML",
}

DOCUMENT_MIME_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "DOCX",
    "text/markdown": "MD",
    "text/x-markdown": "MD",
    "text/plain": "TXT",
    "text/csv": "CSV",
    "text/tab-separated-values": "TSV",
    "application/json": "JSON",
    "application/xml": "XML",
    "text/xml": "XML",
    "text/html": "HTML",
}

DOCUMENT_TARGETS = {
    "DOCX": ["MD", "TXT", "HTML", "DAT", "LOG"],
    "MD": ["DOCX", "TXT", "HTML", "DAT", "LOG"],
    "TXT": ["DOCX", "MD", "HTML", "DAT", "LOG", "CSV", "JSON"],
    "DAT": ["TXT", "DOCX", "MD", "HTML", "LOG", "CSV", "JSON"],
    "LOG": ["TXT", "DOCX", "MD", "HTML", "DAT"],
    "CSV": ["JSON", "TSV", "MD", "DOCX", "TXT", "HTML", "DAT"],
    "TSV": ["CSV", "JSON", "MD", "DOCX", "TXT", "HTML", "DAT"],
    "JSON": ["CSV", "TXT", "MD", "DOCX", "DAT", "HTML"],
    "XML": ["JSON", "TXT", "MD", "DOCX", "DAT", "HTML"],
    "HTML": ["MD", "TXT", "DOCX", "DAT", "LOG"],
}


def normalize_format(fmt: str) -> str:
    """Normalizes format string to standard uppercase key."""
    if not fmt:
        return ""
    cleaned = fmt.upper().strip().lstrip(".")
    if cleaned in ("JPEG", "JPG"):
        return "JPG"
    if cleaned in ("TIF", "TIFF"):
        return "TIFF"
    if cleaned == "MARKDOWN":
        return "MD"
    if cleaned == "HTM":
        return "HTML"
    return cleaned


def detect_file_type(filename: str | None = None, mime_type: str | None = None) -> tuple[str | None, str | None]:
    """
    Detects file category and format.
    Returns (category, format_name) where category is 'image' or 'document'.
    """
    ext = os.path.splitext(filename)[1].lower().lstrip(".") if filename else ""

    # Check extension first
    if ext in IMAGE_EXTENSIONS:
        return "image", IMAGE_EXTENSIONS[ext]
    if ext in DOCUMENT_EXTENSIONS:
        return "document", DOCUMENT_EXTENSIONS[ext]

    # Check MIME type
    if mime_type:
        mime = mime_type.lower()
        if mime in IMAGE_MIME_TYPES or mime.startswith("image/"):
            for m_key, f_val in IMAGE_MIME_TYPES.items():
                if m_key in mime:
                    return "image", f_val
            if "png" in mime:
                return "image", "PNG"
            if "jpeg" in mime or "jpg" in mime:
                return "image", "JPG"
            if "webp" in mime:
                return "image", "WEBP"
            return "image", "PNG"

        if mime in DOCUMENT_MIME_TYPES:
            return "document", DOCUMENT_MIME_TYPES[mime]
        if "wordprocessingml" in mime:
            return "document", "DOCX"
        if "json" in mime:
            return "document", "JSON"
        if "csv" in mime:
            return "document", "CSV"
        if "xml" in mime:
            return "document", "XML"
        if "html" in mime:
            return "document", "HTML"
        if "text" in mime:
            return "document", "TXT"

    return None, None


def detect_image_format(filename: str | None = None, mime_type: str | None = None) -> str | None:
    cat, fmt = detect_file_type(filename, mime_type)
    return fmt if cat == "image" else None


def format_size(size_bytes: int) -> str:
    """Formats file size into human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} Б"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} КБ"
    return f"{size_bytes / (1024 * 1024):.2f} МБ"


def decode_text(raw_bytes: bytes) -> str:
    """Safely decodes raw bytes into string trying multiple encodings."""
    for enc in ("utf-8", "utf-8-sig", "cp1251", "latin-1"):
        try:
            return raw_bytes.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="replace")


def convert_image(input_bytes: bytes, target_format: str) -> tuple[bytes, str]:
    """
    Converts image raw bytes into the requested target format.
    Returns a tuple of (converted_bytes, file_extension).
    """
    target = normalize_format(target_format)
    if target not in SUPPORTED_IMAGE_FORMATS:
        raise ValueError(f"Неподдерживаемый целевой формат: {target_format}")

    with Image.open(io.BytesIO(input_bytes)) as img:
        output_io = io.BytesIO()
        ext = SUPPORTED_IMAGE_FORMATS[target]["ext"]

        if target == "JPG":
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                img_rgba = img.convert("RGBA")
                bg.paste(img_rgba, mask=img_rgba.split()[3])
                save_img = bg
            elif img.mode != "RGB":
                save_img = img.convert("RGB")
            else:
                save_img = img
            save_img.save(output_io, format="JPEG", quality=95, optimize=True)

        elif target == "PNG":
            img.save(output_io, format="PNG", optimize=True)

        elif target == "WEBP":
            img.save(output_io, format="WEBP", quality=95)

        elif target == "BMP":
            if img.mode in ("RGBA", "LA"):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                img_rgba = img.convert("RGBA")
                bg.paste(img_rgba, mask=img_rgba.split()[3])
                save_img = bg
            elif img.mode != "RGB":
                save_img = img.convert("RGB")
            else:
                save_img = img
            save_img.save(output_io, format="BMP")

        elif target == "TIFF":
            img.save(output_io, format="TIFF")

        elif target == "ICO":
            ico_img = img.copy()
            if ico_img.width > 256 or ico_img.height > 256:
                ico_img.thumbnail((256, 256), Image.Resampling.LANCZOS)
            if ico_img.mode not in ("RGBA", "RGB"):
                ico_img = ico_img.convert("RGBA")
            ico_img.save(output_io, format="ICO")

        elif target == "PDF":
            if img.mode in ("RGBA", "LA"):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                img_rgba = img.convert("RGBA")
                bg.paste(img_rgba, mask=img_rgba.split()[3])
                save_img = bg
            elif img.mode != "RGB":
                save_img = img.convert("RGB")
            else:
                save_img = img
            save_img.save(output_io, format="PDF", resolution=100.0)

        elif target == "GIF":
            img.save(output_io, format="GIF")

        return output_io.getvalue(), ext


# ==========================================
# Document / Text conversion utilities
# ==========================================

def _add_markdown_runs_to_paragraph(paragraph, text: str) -> None:
    pattern = re.compile(r'(`[^`]+`|\*\*\*[^*]+\*\*\*|\*\*[^*]+\*\*|\*[^*]+\*|___[^_]+___|__[^_]+__|_[^_]+_)')
    parts = pattern.split(text)
    for part in parts:
        if not part:
            continue
        if part.startswith("`") and part.endswith("`") and len(part) >= 2:
            r = paragraph.add_run(part[1:-1])
            r.font.name = "Courier New"
        elif (part.startswith("***") and part.endswith("***") and len(part) >= 6) or \
             (part.startswith("___") and part.endswith("___") and len(part) >= 6):
            r = paragraph.add_run(part[3:-3])
            r.bold = True
            r.italic = True
        elif (part.startswith("**") and part.endswith("**") and len(part) >= 4) or \
             (part.startswith("__") and part.endswith("__") and len(part) >= 4):
            r = paragraph.add_run(part[2:-2])
            r.bold = True
        elif (part.startswith("*") and part.endswith("*") and len(part) >= 2) or \
             (part.startswith("_") and part.endswith("_") and len(part) >= 2):
            r = paragraph.add_run(part[1:-1])
            r.italic = True
        else:
            paragraph.add_run(part)


def markdown_to_docx(md_text: str) -> bytes:
    """Converts Markdown text into a styled DOCX document."""
    doc = Document()
    lines = md_text.splitlines()
    in_code_block = False
    code_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code_block:
                p = doc.add_paragraph("\n".join(code_lines))
                for run in p.runs:
                    run.font.name = "Courier New"
                code_lines = []
                in_code_block = False
            else:
                in_code_block = True
                code_lines = []
            continue

        if in_code_block:
            code_lines.append(line)
            continue

        if stripped.startswith("# "):
            doc.add_heading(stripped[2:], level=1)
        elif stripped.startswith("## "):
            doc.add_heading(stripped[3:], level=2)
        elif stripped.startswith("### "):
            doc.add_heading(stripped[4:], level=3)
        elif stripped.startswith("#### "):
            doc.add_heading(stripped[5:], level=4)
        elif stripped.startswith(("- ", "* ", "+ ")):
            doc.add_paragraph(stripped[2:], style='List Bullet')
        elif len(stripped) > 2 and stripped[0].isdigit() and stripped[1:3] in (". ", ") "):
            doc.add_paragraph(stripped[3:], style='List Number')
        elif stripped.startswith("> "):
            doc.add_paragraph(stripped[2:], style='Quote' if 'Quote' in doc.styles else 'Normal')
        elif stripped == "":
            doc.add_paragraph("")
        else:
            p = doc.add_paragraph()
            _add_markdown_runs_to_paragraph(p, line)

    out_io = io.BytesIO()
    doc.save(out_io)
    return out_io.getvalue()


def docx_to_markdown(docx_bytes: bytes) -> str:
    """Converts a DOCX document into Markdown string."""
    doc = Document(io.BytesIO(docx_bytes))
    md_parts = []

    for p in doc.paragraphs:
        style_name = p.style.name.lower() if p.style else ""
        text = ""
        for r in p.runs:
            run_text = r.text
            if not run_text:
                continue
            if r.bold and r.italic:
                run_text = f"***{run_text}***"
            elif r.bold:
                run_text = f"**{run_text}**"
            elif r.italic:
                run_text = f"*{run_text}*"
            text += run_text

        if not text.strip():
            md_parts.append("")
            continue

        if "heading 1" in style_name:
            md_parts.append(f"# {text.strip()}")
        elif "heading 2" in style_name:
            md_parts.append(f"## {text.strip()}")
        elif "heading 3" in style_name:
            md_parts.append(f"### {text.strip()}")
        elif "heading 4" in style_name:
            md_parts.append(f"#### {text.strip()}")
        elif "bullet" in style_name or "list bullet" in style_name:
            md_parts.append(f"- {text.strip()}")
        elif "number" in style_name or "list number" in style_name:
            md_parts.append(f"1. {text.strip()}")
        elif "quote" in style_name:
            md_parts.append(f"> {text.strip()}")
        else:
            md_parts.append(text)

    for table in doc.tables:
        if not table.rows:
            continue
        table_lines = []
        headers = [cell.text.strip().replace("\n", " ") for cell in table.rows[0].cells]
        table_lines.append("| " + " | ".join(headers) + " |")
        table_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
        for row in table.rows[1:]:
            cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
            table_lines.append("| " + " | ".join(cells) + " |")
        md_parts.append("\n" + "\n".join(table_lines) + "\n")

    return "\n".join(md_parts)


def docx_to_text(docx_bytes: bytes) -> str:
    """Extracts plain text from DOCX."""
    doc = Document(io.BytesIO(docx_bytes))
    lines = []
    for p in doc.paragraphs:
        lines.append(p.text)
    for table in doc.tables:
        for row in table.rows:
            lines.append("\t".join(cell.text.strip() for cell in row.cells))
    return "\n".join(lines)


def text_to_docx(text: str) -> bytes:
    """Converts plain text to DOCX."""
    doc = Document()
    for line in text.splitlines():
        doc.add_paragraph(line)
    out_io = io.BytesIO()
    doc.save(out_io)
    return out_io.getvalue()


def markdown_to_html(md_text: str) -> str:
    """Converts Markdown to standalone HTML document."""
    html_body = markdown.markdown(md_text, extensions=['extra', 'tables', 'fenced_code'])
    return (
        "<!DOCTYPE html>\n"
        "<html>\n<head>\n<meta charset=\"utf-8\">\n"
        "<style>body { font-family: sans-serif; line-height: 1.6; max-width: 800px; margin: 40px auto; padding: 0 20px; } table { border-collapse: collapse; width: 100%; } th, td { border: 1px solid #ddd; padding: 8px; } tr:nth-child(even){ background-color: #f2f2f2; } th { background-color: #333; color: white; } pre { background: #f4f4f4; padding: 10px; border-radius: 5px; }</style>\n"
        "</head>\n<body>\n"
        f"{html_body}\n"
        "</body>\n</html>"
    )


def html_to_markdown(html_text: str) -> str:
    """Simple HTML to Markdown converter."""
    # Convert headings
    text = re.sub(r'<h1[^>]*>(.*?)</h1>', r'# \1\n\n', html_text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<h2[^>]*>(.*?)</h2>', r'## \1\n\n', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<h3[^>]*>(.*?)</h3>', r'### \1\n\n', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<h4[^>]*>(.*?)</h4>', r'#### \1\n\n', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<strong[^>]*>(.*?)</strong>|<b[^>]*>(.*?)</b>', r'**\1\2**', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<em[^>]*>(.*?)</em>|<i[^>]*>(.*?)</i>', r'*\1\2*', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<code[^>]*>(.*?)</code>', r'`\1`', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<li[^>]*>(.*?)</li>', r'- \1\n', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<p[^>]*>(.*?)</p>', r'\1\n\n', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    return text.strip()


def html_to_text(html_text: str) -> str:
    """Strips HTML tags and extracts text."""
    clean = re.sub(r'<br\s*/?>', '\n', html_text, flags=re.IGNORECASE)
    clean = re.sub(r'</p>', '\n\n', clean, flags=re.IGNORECASE)
    clean = re.sub(r'</h1>|</h2>|</h3>|</h4>', '\n\n', clean, flags=re.IGNORECASE)
    clean = re.sub(r'</li>', '\n', clean, flags=re.IGNORECASE)
    clean = re.sub(r'<[^>]+>', '', clean)
    return clean.strip()


def csv_to_json(text: str) -> str:
    """Converts CSV/TSV to formatted JSON string."""
    delimiter = "\t" if "\t" in text and "," not in text else (";" if ";" in text and "," not in text else ",")
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    rows = list(reader)
    if not rows:
        reader2 = csv.reader(io.StringIO(text), delimiter=delimiter)
        rows = list(reader2)
    return json.dumps(rows, ensure_ascii=False, indent=2)


def json_to_csv(text: str) -> str:
    """Converts JSON to CSV string."""
    data = json.loads(text)
    out = io.StringIO()
    if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
        keys = list(data[0].keys())
        writer = csv.DictWriter(out, fieldnames=keys)
        writer.writeheader()
        writer.writerows(data)
    elif isinstance(data, dict):
        writer = csv.writer(out)
        writer.writerow(["Key", "Value"])
        for k, v in data.items():
            val = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
            writer.writerow([k, val])
    elif isinstance(data, list):
        writer = csv.writer(out)
        writer.writerow(["Item"])
        for item in data:
            writer.writerow([item])
    else:
        writer = csv.writer(out)
        writer.writerow(["Value"])
        writer.writerow([data])
    return out.getvalue()


def csv_to_markdown(text: str) -> str:
    """Converts CSV to Markdown table."""
    delimiter = "\t" if "\t" in text and "," not in text else (";" if ";" in text and "," not in text else ",")
    reader = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    if not reader:
        return text
    md_lines = []
    headers = [cell.strip().replace("\n", " ") for cell in reader[0]]
    md_lines.append("| " + " | ".join(headers) + " |")
    md_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in reader[1:]:
        row_cells = [c.strip().replace("\n", " ") for c in row]
        if len(row_cells) < len(headers):
            row_cells.extend([""] * (len(headers) - len(row_cells)))
        md_lines.append("| " + " | ".join(row_cells[:len(headers)]) + " |")
    return "\n".join(md_lines)


def csv_to_docx(text: str) -> bytes:
    """Converts CSV table to Word DOCX document."""
    delimiter = "\t" if "\t" in text and "," not in text else (";" if ";" in text and "," not in text else ",")
    reader = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    doc = Document()
    if not reader:
        doc.add_paragraph("Пустая таблица")
    else:
        cols_count = max(len(r) for r in reader) if reader else 1
        table = doc.add_table(rows=len(reader), cols=cols_count)
        table.style = 'Table Grid'
        for i, row in enumerate(reader):
            for j, cell_text in enumerate(row):
                if j < cols_count:
                    cell = table.cell(i, j)
                    cell.text = cell_text
                    if i == 0:
                        for p in cell.paragraphs:
                            for r in p.runs:
                                r.bold = True
    out_io = io.BytesIO()
    doc.save(out_io)
    return out_io.getvalue()


def convert_document(input_bytes: bytes, source_format: str, target_format: str) -> tuple[bytes, str]:
    """
    Converts document or text between formats.
    Returns (output_bytes, file_extension).
    """
    src = normalize_format(source_format)
    target = normalize_format(target_format)

    # 1. DOCX as source
    if src == "DOCX":
        if target == "MD":
            md_str = docx_to_markdown(input_bytes)
            return md_str.encode("utf-8"), "md"
        elif target in ("TXT", "DAT", "LOG"):
            txt_str = docx_to_text(input_bytes)
            ext = SUPPORTED_DOCUMENT_FORMATS[target]["ext"]
            return txt_str.encode("utf-8"), ext
        elif target == "HTML":
            md_str = docx_to_markdown(input_bytes)
            html_str = markdown_to_html(md_str)
            return html_str.encode("utf-8"), "html"
        else:
            txt_str = docx_to_text(input_bytes)
            ext = SUPPORTED_DOCUMENT_FORMATS.get(target, {}).get("ext", target.lower())
            return txt_str.encode("utf-8"), ext

    # Non-DOCX source: decode text
    text_content = decode_text(input_bytes)

    # 2. Target DOCX
    if target == "DOCX":
        if src == "MD":
            return markdown_to_docx(text_content), "docx"
        elif src in ("CSV", "TSV"):
            return csv_to_docx(text_content), "docx"
        elif src == "HTML":
            md_from_html = html_to_markdown(text_content)
            return markdown_to_docx(md_from_html), "docx"
        else:
            return text_to_docx(text_content), "docx"

    # 3. MD as source
    if src == "MD":
        if target == "HTML":
            return markdown_to_html(text_content).encode("utf-8"), "html"
        elif target in ("TXT", "DAT", "LOG"):
            ext = SUPPORTED_DOCUMENT_FORMATS[target]["ext"]
            return text_content.encode("utf-8"), ext

    # 4. Target MD
    if target == "MD":
        if src in ("CSV", "TSV"):
            return csv_to_markdown(text_content).encode("utf-8"), "md"
        elif src == "HTML":
            return html_to_markdown(text_content).encode("utf-8"), "md"
        elif src == "JSON":
            try:
                parsed = json.loads(text_content)
                pretty_json = json.dumps(parsed, ensure_ascii=False, indent=2)
                return f"```json\n{pretty_json}\n```\n".encode("utf-8"), "md"
            except Exception:
                return text_content.encode("utf-8"), "md"
        else:
            return text_content.encode("utf-8"), "md"

    # 5. CSV / TSV
    if src == "CSV":
        if target == "JSON":
            return csv_to_json(text_content).encode("utf-8"), "json"
        elif target == "TSV":
            tsv_str = text_content.replace(",", "\t")
            return tsv_str.encode("utf-8"), "tsv"
        elif target == "HTML":
            md_str = csv_to_markdown(text_content)
            return markdown_to_html(md_str).encode("utf-8"), "html"
        elif target in ("TXT", "DAT", "LOG"):
            ext = SUPPORTED_DOCUMENT_FORMATS[target]["ext"]
            return text_content.encode("utf-8"), ext

    if src == "TSV":
        if target == "CSV":
            csv_str = text_content.replace("\t", ",")
            return csv_str.encode("utf-8"), "csv"
        elif target == "JSON":
            return csv_to_json(text_content).encode("utf-8"), "json"
        elif target == "HTML":
            md_str = csv_to_markdown(text_content)
            return markdown_to_html(md_str).encode("utf-8"), "html"
        elif target in ("TXT", "DAT", "LOG"):
            ext = SUPPORTED_DOCUMENT_FORMATS[target]["ext"]
            return text_content.encode("utf-8"), ext

    # 6. JSON
    if src == "JSON":
        if target == "CSV":
            return json_to_csv(text_content).encode("utf-8"), "csv"
        elif target == "HTML":
            try:
                pretty = json.dumps(json.loads(text_content), ensure_ascii=False, indent=2)
            except Exception:
                pretty = text_content
            html = f"<!DOCTYPE html><html><head><meta charset='utf-8'></head><body><pre><code>{pretty}</code></pre></body></html>"
            return html.encode("utf-8"), "html"
        elif target in ("TXT", "DAT", "LOG"):
            ext = SUPPORTED_DOCUMENT_FORMATS[target]["ext"]
            return text_content.encode("utf-8"), ext

    # 7. HTML
    if src == "HTML":
        if target in ("TXT", "DAT", "LOG"):
            plain = html_to_text(text_content)
            ext = SUPPORTED_DOCUMENT_FORMATS[target]["ext"]
            return plain.encode("utf-8"), ext

    # 8. General fallback for text files
    ext = SUPPORTED_DOCUMENT_FORMATS.get(target, {}).get("ext", target.lower())
    return text_content.encode("utf-8"), ext
