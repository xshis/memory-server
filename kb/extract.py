from __future__ import annotations

import fnmatch
import re
from pathlib import Path

TEXT_EXTS = {
    ".md", ".markdown", ".txt", ".text", ".log",
    ".json", ".jsonl", ".csv", ".tsv", ".xml", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".conf", ".html", ".htm", ".css", ".scss", ".less",
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".java", ".kt", ".go", ".rs", ".c", ".h", ".cpp", ".hpp", ".cs",
    ".rb", ".php", ".swift", ".sql", ".sh", ".bash", ".zsh",
    ".ps1", ".bat", ".cmd", ".r", ".lua", ".pl",
}

OFFICE_EXTS = {".pdf", ".docx", ".xlsx", ".pptx"}

SENSITIVE_NAMES = {
    ".env", "id_rsa", "id_ed25519", "credentials", "cookies",
    "login data", "web data",
}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".kdbx"}


def is_sensitive(path: Path) -> bool:
    name = path.name.lower()
    if name in SENSITIVE_NAMES:
        return True
    if any(name.endswith(suffix) for suffix in SENSITIVE_SUFFIXES):
        return True
    if name.startswith(".env."):
        return True
    return False


def is_excluded(path: Path, exclude_globs: list[str]) -> bool:
    name = path.name
    return any(fnmatch.fnmatch(name, pattern) for pattern in exclude_globs)


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def _extract_pdf(path: Path) -> tuple[str, str, str]:
    try:
        from pypdf import PdfReader
    except ImportError:
        return "", "unsupported", "未安装 pypdf，PDF 只索引文件名"
    try:
        reader = PdfReader(str(path))
        parts = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        return "\n\n".join(parts), "indexed", ""
    except Exception as exc:  # noqa: BLE001
        return "", "error", f"PDF 解析失败：{exc}"


def _extract_docx(path: Path) -> tuple[str, str, str]:
    try:
        from docx import Document
    except ImportError:
        return "", "unsupported", "未安装 python-docx，DOCX 只索引文件名"
    try:
        doc = Document(str(path))
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
        return "\n".join(parts), "indexed", ""
    except Exception as exc:  # noqa: BLE001
        return "", "error", f"DOCX 解析失败：{exc}"


def _extract_xlsx(path: Path) -> tuple[str, str, str]:
    try:
        from openpyxl import load_workbook
    except ImportError:
        return "", "unsupported", "未安装 openpyxl，XLSX 只索引文件名"
    try:
        workbook = load_workbook(str(path), read_only=True, data_only=True)
        parts = []
        for sheet in workbook.worksheets:
            parts.append(f"## {sheet.title}")
            for row in sheet.iter_rows(values_only=True):
                cells = [str(value) for value in row if value is not None]
                if cells:
                    parts.append(" | ".join(cells))
        workbook.close()
        return "\n".join(parts), "indexed", ""
    except Exception as exc:  # noqa: BLE001
        return "", "error", f"XLSX 解析失败：{exc}"


def _extract_pptx(path: Path) -> tuple[str, str, str]:
    try:
        from pptx import Presentation
    except ImportError:
        return "", "unsupported", "未安装 python-pptx，PPTX 只索引文件名"
    try:
        presentation = Presentation(str(path))
        parts = []
        for index, slide in enumerate(presentation.slides, 1):
            parts.append(f"## 第 {index} 页")
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    parts.append(shape.text.strip())
        return "\n".join(parts), "indexed", ""
    except Exception as exc:  # noqa: BLE001
        return "", "error", f"PPTX 解析失败：{exc}"


def extract_text(path: Path) -> tuple[str, str, str]:
    """Return (text, status, error).

    status: indexed / unsupported / error
    """
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix == ".docx":
        return _extract_docx(path)
    if suffix == ".xlsx":
        return _extract_xlsx(path)
    if suffix == ".pptx":
        return _extract_pptx(path)
    if suffix in TEXT_EXTS:
        try:
            return _decode(path.read_bytes()), "indexed", ""
        except OSError as exc:
            return "", "error", f"读取失败：{exc}"
    return "", "unsupported", ""


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk_text(text: str, size: int = 1200, overlap: int = 200) -> list[str]:
    text = normalize_text(text)
    if not text:
        return []
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    chunks: list[str] = []
    buffer = ""

    for paragraph in paragraphs:
        if len(paragraph) > size:
            if buffer:
                chunks.append(buffer)
                buffer = ""
            start = 0
            while start < len(paragraph):
                chunks.append(paragraph[start:start + size])
                start += max(1, size - overlap)
            continue

        candidate = f"{buffer}\n\n{paragraph}".strip() if buffer else paragraph
        if len(candidate) <= size:
            buffer = candidate
        else:
            if buffer:
                chunks.append(buffer)
            buffer = paragraph

    if buffer:
        chunks.append(buffer)
    return chunks
