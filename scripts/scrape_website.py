"""Scrape a public sitemap into clean text for website-chatbot knowledge.

Usage:
  python scripts/scrape_website.py https://comez.in/sitemap.xml \
      --out ../knowledge_sources/comez --limit 120
"""
from __future__ import annotations

import argparse
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from xml.etree import ElementTree as ET

UA = "SymmaKnowledgeBot/1.0 (+https://symma.shop; contact: support@symma.shop)"


def parse_sitemap(url: str) -> list[str]:
    resp = requests.get(url, timeout=30, headers={"User-Agent": UA})
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    urls = [
        (node.text or "").strip()
        for node in root.iter()
        if node.tag.split("}")[-1] == "loc" and (node.text or "").strip()
    ]
    # Sitemap indexes point to child sitemaps.
    children: list[str] = []
    for loc in urls:
        if loc.endswith(".xml"):
            children.extend(parse_sitemap(loc))
    return [u for u in urls if not u.endswith(".xml")] + children


def clean_text(html: str, url: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    # Decode Cloudflare-obfuscated emails before stripping scripts.
    for tag in soup.select("[data-cfemail]"):
        encoded = tag.get("data-cfemail") or ""
        try:
            key = int(encoded[:2], 16)
            email = "".join(
                chr(int(encoded[i : i + 2], 16) ^ key)
                for i in range(2, len(encoded), 2)
            )
            tag.string = email
        except (ValueError, TypeError):
            pass
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "form"]):
        tag.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    for tag in root.find_all(["nav", "header", "footer", "aside"]):
        tag.decompose()

    title = ""
    h1 = root.find("h1")
    if h1:
        title = h1.get_text(" ", strip=True)
    if not title and soup.title:
        title = soup.title.get_text(" ", strip=True)

    text = root.get_text("\n", strip=True)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return title[:300], text.strip()


def fetch_one(url: str) -> dict:
    try:
        resp = requests.get(url, timeout=30, headers={"User-Agent": UA})
        if resp.status_code != 200:
            return {"url": url, "status": resp.status_code, "title": "", "text": ""}
        title, text = clean_text(resp.text, url)
        return {"url": url, "status": 200, "title": title, "text": text}
    except Exception as exc:
        return {"url": url, "status": 0, "title": "", "text": "", "error": str(exc)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sitemap")
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    urls = parse_sitemap(args.sitemap)
    seen: set[str] = set()
    unique: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    unique = unique[: args.limit]
    print(f"Found {len(unique)} URLs")

    pages: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(fetch_one, u): u for u in unique}
        for idx, future in enumerate(as_completed(futures), start=1):
            page = future.result()
            pages.append(page)
            print(f"[{idx}/{len(unique)}] {page.get('status')} {page['url']} ({len(page.get('text') or '')} chars)")
            time.sleep(0.15)

    pages.sort(key=lambda p: unique.index(p["url"]) if p["url"] in unique else 0)
    (out_dir / "pages.json").write_text(json.dumps(pages, ensure_ascii=False, indent=2))

    with (out_dir / "combined.md").open("w") as fh:
        for page in pages:
            if not page.get("text"):
                continue
            fh.write(f"\n\n# {page.get('title') or page['url']}\n")
            fh.write(f"Source: {page['url']}\n\n")
            fh.write(page["text"])
            fh.write("\n")
    print(f"Wrote {len(pages)} pages to {out_dir}")


if __name__ == "__main__":
    main()
