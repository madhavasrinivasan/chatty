# Install dependencies:
# pip install pypdf reportlab google-cloud-translate

import os
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from google.cloud import translate_v2 as translate

# ─── CONFIG ───────────────────────────────────────────────────────────────────
INPUT_PDF     = "905614.pdf"
OUTPUT_PDF    = "translated_english.pdf"
# Set your Google Cloud credentials JSON path:

# ──────────────────────────────────────────────────────────────────────────────

def translate_text(client, text: str) -> str:
    """Translate Tamil text to English using Google Cloud Translation API."""
    if not text.strip():
        return ""
    # API has a 30,000 byte limit per request — chunk if needed
    MAX_BYTES = 25000
    encoded = text.encode("utf-8")
    if len(encoded) <= MAX_BYTES:
        result = client.translate(text, source_language="ta", target_language="en")
        return result["translatedText"]
    # Split into chunks if page text is very large
    chunks = []
    current_chunk = ""
    for line in text.splitlines(keepends=True):
        if len((current_chunk + line).encode("utf-8")) > MAX_BYTES:
            if current_chunk:
                result = client.translate(current_chunk, source_language="ta", target_language="en")
                chunks.append(result["translatedText"])
            current_chunk = line
        else:
            current_chunk += line
    if current_chunk:
        result = client.translate(current_chunk, source_language="ta", target_language="en")
        chunks.append(result["translatedText"])
    return " ".join(chunks)


def build_output_pdf(pages_text: list[tuple[int, str]], output_path: str):
    """Save translated text as a formatted PDF."""
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=20*mm,
        rightMargin=20*mm,
        topMargin=20*mm,
        bottomMargin=20*mm,
    )
    styles = getSampleStyleSheet()
    heading_style = ParagraphStyle(
        "PageHeading",
        parent=styles["Heading2"],
        spaceAfter=6,
        textColor="#333333",
    )
    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontSize=11,
        leading=16,
        spaceAfter=8,
    )
    story = []
    for page_num, text in pages_text:
        story.append(Paragraph(f"Page {page_num}", heading_style))
        story.append(Spacer(1, 4))
        # Split into paragraphs for clean formatting
        for para in text.split("\n"):
            para = para.strip()
            if para:
                # Escape XML special chars for ReportLab
                para = para.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                story.append(Paragraph(para, body_style))
        story.append(PageBreak())
    doc.build(story)


def main():
    print("Initialising Google Translate client...")
    translate_client = translate.Client(client_options={"api_key": "AIzaSyCJr5YoZYVZNyz6Zn9j-rbCQxDJeh9aqLs"})

    print(f"Reading PDF: {INPUT_PDF}")
    reader = PdfReader(INPUT_PDF)
    total_pages = len(reader.pages)
    print(f"Total pages: {total_pages}")

    translated_pages = []
    for i, page in enumerate(reader.pages, start=1):
        print(f"Translating page {i}/{total_pages}...", end=" ", flush=True)
        raw_text = page.extract_text() or ""
        if not raw_text.strip():
            print("(empty, skipped)")
            translated_pages.append((i, "[No text found on this page]"))
            continue
        english_text = translate_text(translate_client, raw_text)
        translated_pages.append((i, english_text))
        print("done")

    print(f"\nBuilding output PDF: {OUTPUT_PDF}")
    build_output_pdf(translated_pages, OUTPUT_PDF)
    print(f"✅ Done! Saved to: {OUTPUT_PDF}")


if __name__ == "__main__":
    main()