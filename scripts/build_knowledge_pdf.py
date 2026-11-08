"""Build a compact PDF from scraped pages.json for website knowledge upload.

Usage:
  python scripts/build_knowledge_pdf.py \
      --pages ../knowledge_sources/comez/pages.json \
      --out ../knowledge_sources/comez/comez-knowledge.pdf \
      --title "Comez Website Knowledge"
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)


def build(pages: list[dict], out_path: Path, title: str, max_chars: int) -> None:
    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=title,
    )
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "Body",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=8.4,
        leading=10.4,
        spaceAfter=2,
    )
    h1 = ParagraphStyle(
        "H1",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=18,
        spaceAfter=6,
        textColor="#0B1220",
    )
    h2 = ParagraphStyle(
        "H2",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=10.5,
        leading=13,
        spaceBefore=7,
        spaceAfter=3,
        textColor="#0B1220",
    )
    meta = ParagraphStyle(
        "Meta",
        parent=body,
        fontSize=7.2,
        leading=9,
        textColor="#555555",
        spaceAfter=4,
    )

    story: list = [
        Paragraph(escape(title), h1),
        Paragraph(
            "Grounded reference content for a website support assistant. "
            "Answers should be based only on this document.",
            meta,
        ),
        Spacer(1, 4 * mm),
    ]
    for page in pages:
        text = (page.get("text") or "").strip()
        if not text:
            continue
        heading = (page.get("title") or page.get("url") or "Page").strip()
        story.append(Paragraph(escape(heading), h2))
        story.append(Paragraph(escape(page.get("url") or ""), meta))
        # Keep the PDF compact for ingestion; chunks get split again downstream.
        excerpt = text[:max_chars]
        for block in excerpt.split("\n"):
            block = block.strip()
            if not block:
                continue
            story.append(Paragraph(escape(block), body))
        story.append(Spacer(1, 2.5 * mm))
    doc.build(story)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--title", default="Website Knowledge")
    parser.add_argument("--max-chars", type=int, default=5000)
    args = parser.parse_args()

    pages = json.loads(Path(args.pages).read_text())
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    build(pages, out, args.title, args.max_chars)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
