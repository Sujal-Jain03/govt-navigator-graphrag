from pathlib import Path
from pypdf import PdfReader
import pdfplumber


def load_pdf(path: str | Path) -> str:
    """PDF ka poora text nikalta hai. pypdf primary, blank pages pe pdfplumber fallback."""
    path = Path(path)
    reader = PdfReader(str(path))
    texts = []
    blank_pages = []
    for i, page in enumerate(reader.pages):
        t = page.extract_text() or ""
        texts.append(t)
        if not t.strip():
            blank_pages.append(i)
    if blank_pages:
        with pdfplumber.open(str(path)) as pdf:
            for i in blank_pages:
                texts[i] = pdf.pages[i].extract_text() or ""
    return "\n".join(texts)
