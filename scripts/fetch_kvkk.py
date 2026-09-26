#!/usr/bin/env python3
"""Build the article-aligned Turkish/English KVKK corpus.

Downloads Law No. 6698 (KVKK) and three KVKK by-laws in Turkish and in the
KVKK Authority's English translation, splits both into one markdown file per
article, and writes only the articles present in BOTH languages to:

    eval/datasets/kvkk-tr/corpus/   (Turkish)
    eval/datasets/kvkk-en/corpus/   (English)

The same filename in both directories holds the same legal provision.

Usage:
    python/.venv/bin/python scripts/fetch_kvkk.py [--cache-dir DIR] [--refresh]

Requirements: beautifulsoup4 (in python/.venv) and the poppler `pdftotext`
binary for the one English PDF source (falls back to pypdf if missing, with
noticeably worse word spacing).
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup

USER_AGENT = "AletheiaResearch/0.1"
REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_ROOT = REPO_ROOT / "eval" / "datasets"

MEVZUAT_IFRAME = (
    "https://www.mevzuat.gov.tr/anasayfa/MevzuatFihristDetayIframe"
    "?MevzuatTur={tur}&MevzuatNo={no}&MevzuatTertip=5"
)


@dataclass(frozen=True)
class Source:
    url: str
    kind: str  # "mevzuat" | "kvkk" | "rg" | "pdf"


@dataclass(frozen=True)
class Document:
    prefix: str
    name_tr: str
    name_en: str
    tr: Source
    en: Source


DOCUMENTS = (
    Document(
        prefix="6698",
        name_tr="6698 sayılı Kişisel Verilerin Korunması Kanunu",
        name_en="Personal Data Protection Law No. 6698",
        tr=Source(MEVZUAT_IFRAME.format(tur=1, no=6698), "mevzuat"),
        en=Source("https://www.kvkk.gov.tr/Icerik/6649/Personal-Data-Protection-Law", "kvkk"),
    ),
    Document(
        prefix="silme",
        name_tr="Kişisel Verilerin Silinmesi, Yok Edilmesi veya Anonim Hale Getirilmesi Hakkında Yönetmelik",
        name_en="By-Law on Erasure, Destruction or Anonymization of Personal Data",
        tr=Source(MEVZUAT_IFRAME.format(tur=7, no=24038), "mevzuat"),
        en=Source(
            "https://www.kvkk.gov.tr/Icerik/6636/By-Law-on-Erasure-Destruction-or-Anonymization-of-Personal-Data",
            "kvkk",
        ),
    ),
    Document(
        prefix="sicil",
        name_tr="Veri Sorumluları Sicili Hakkında Yönetmelik",
        name_en="By-Law on Data Controllers' Registry",
        tr=Source(MEVZUAT_IFRAME.format(tur=7, no=24276), "mevzuat"),
        en=Source("https://www.kvkk.gov.tr/Icerik/6635/By-Law-On-Data-Controllers-Registry", "kvkk"),
    ),
    Document(
        prefix="aktarim",
        name_tr="Kişisel Verilerin Yurt Dışına Aktarılmasına İlişkin Usul ve Esaslar Hakkında Yönetmelik",
        name_en="By-Law on the Procedures and Principles for the Transfer of Personal Data Abroad",
        tr=Source("https://www.resmigazete.gov.tr/eskiler/2024/07/20240710-2.htm", "rg"),
        en=Source(
            "https://www.kvkk.gov.tr/Icerik/7997/The-Procedures-And-Principles-For-The-Transfer-Of-Personal-Data-Abroad",
            "pdf",
        ),
    ),
)

# --- regexes -----------------------------------------------------------------

ARTICLE_RE = {
    "tr": re.compile(r"^(GEÇİCİ MADDE|MADDE)\s+(\d+)\s*(?:[-–—]\s*|(?=\()|$)"),
    "en": re.compile(
        r"^(PROVISIONAL ARTICLE|ARTICLE)\s+(\d+)\s*(?:[-–—]\s*|(?=\()|$)", re.IGNORECASE
    ),
}
EMBEDDED_ARTICLE_RE = re.compile(r"\s((?:GEÇİCİ )?MADDE|(?:PROVISIONAL )?ARTICLE)\s+\d+\s*[-–—]")
CHAPTER_RE = re.compile(
    r"(BÖLÜM$|^(PART|CHAPTER)\s+[IVXLC\w]+$|^\w+ CHAPTER$|^CHAPTER \w+$)", re.IGNORECASE
)
END_RE = re.compile(
    r"^(\(I\) SAYILI CETVEL|TABLE NO|Yönetmeliğin Yayımlandığı Resm|The By-[Ll]aw published)"
)
PARA_START_RE = re.compile(r"^(\(\d+\)|\d+\)|[a-zçğıöşü]{1,2}\)\s|(PROVISIONAL )?ARTICLE\s+\d+)")
INLINE_PARA_RE = re.compile(r"(?<=[.;:])\s+(?=\(\d+\)\s+[A-Z])")
FOOTNOTE_REF_RE =re.compile(r"\s*\[\d+\]")
TITLE_BAD_END = (".", ";", ":", ",")
LOWER_ALPHA = "abcdefghijklmnopqrstuvwxyz"


# --- fetching ----------------------------------------------------------------


def _download(url: str) -> bytes:
    """Download with TLS verification. Prefers curl: some .gov.tr hosts serve an
    incomplete certificate chain that curl (system trust store) accepts but
    Python's bundled OpenSSL trust store rejects."""
    if shutil.which("curl"):
        res = subprocess.run(
            ["curl", "-sSfL", "--max-time", "90", "-A", USER_AGENT, url],
            capture_output=True,
        )
        if res.returncode != 0:
            raise OSError(res.stderr.decode("utf-8", "replace").strip())
        return res.stdout
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=90) as resp:
        return resp.read()


def fetch(url: str, dest: Path, refresh: bool) -> bytes:
    if dest.exists() and not refresh:
        return dest.read_bytes()
    for attempt in range(3):
        try:
            data = _download(url)
            dest.write_bytes(data)
            return data
        except OSError as exc:  # curl failure, URLError, timeouts
            if attempt == 2:
                raise RuntimeError(f"failed to fetch {url}: {exc}") from exc
            time.sleep(2 * (attempt + 1))
    raise AssertionError("unreachable")


# --- block extraction (one block = one paragraph of source text) -------------


def norm(text: str) -> str:
    text = text.replace("\xa0", " ").replace("​", "")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([.,;:])", r"\1", text)
    # "i)to carry out" -> "i) to carry out"
    return re.sub(r"^([a-zçğıöşü]{1,2}\)|\(\d+\))(?=[^\s,.;:)])", r"\1 ", text)


def blocks_mevzuat(raw: bytes) -> list[str]:
    soup = BeautifulSoup(raw.decode("utf-8", "replace"), "html.parser")
    out = []
    for p in soup.find_all("p"):
        if "MsoFootnoteText" in (p.get("class") or []):
            continue  # amendment footnotes (Turkish-only editorial notes)
        text = norm(FOOTNOTE_REF_RE.sub("", p.get_text("")))
        if text:
            out.append(text)
    return out


def blocks_rg(raw: bytes) -> list[str]:
    soup = BeautifulSoup(raw.decode("windows-1254", "replace"), "html.parser")
    return [t for t in (norm(p.get_text("")) for p in soup.find_all("p")) if t]


def li_label(li) -> str:
    ol = li.find_parent("ol")
    items = [x for x in ol.find_all("li", recursive=False)]
    idx = int(ol.get("start", 1)) + items.index(li) - 1
    if "lower-alpha" in (ol.get("style") or "") or ol.get("type") == "a":
        return f"({LOWER_ALPHA[idx]})"
    return f"({idx + 1})"


def blocks_kvkk(raw: bytes) -> list[str]:
    soup = BeautifulSoup(raw.decode("utf-8", "replace"), "html.parser")
    root = soup.find("div", class_="news__detail-article") or soup
    out = []
    for el in root.find_all(["p", "li"]):
        if el.find_parent(["p", "li", "table"]):
            continue
        text = norm(el.get_text(""))
        if not text:
            continue
        if el.name == "li" and el.find_parent("ol"):
            text = f"{li_label(el)} {text}"
        if out and is_broken_continuation(text):
            out[-1] = f"{out[-1]} {text}"  # sentence split across two <p> in source
        else:
            # two numbered paragraphs run together in one <p>: "... By-Law. (3) For ..."
            out.extend(INLINE_PARA_RE.split(text))
    return out


def is_broken_continuation(text: str) -> bool:
    """English source sometimes splits one sentence over two <p> elements."""
    return text[0].islower() and not re.match(r"^[a-zçğıöşü]{1,2}\)", text)


def pdf_lines(path: Path) -> list[str]:
    if shutil.which("pdftotext"):
        res = subprocess.run(
            ["pdftotext", "-enc", "UTF-8", str(path), "-"], capture_output=True, check=True
        )
        text = res.stdout.decode("utf-8")
    else:
        print("WARNING: pdftotext not found, using pypdf (spacing artifacts)", file=sys.stderr)
        from pypdf import PdfReader

        text = "\n".join(page.extract_text() for page in PdfReader(str(path)).pages)
    lines = [norm(line) for line in text.replace("\f", "\n").split("\n")]
    return [line for line in lines if line and not re.fullmatch(r"\d+", line)]


def blocks_pdf(path: Path) -> list[str]:
    """Re-join PDF lines into paragraphs using legal-text structure."""
    lines = pdf_lines(path)
    blocks: list[str] = []
    standalone_prev = True
    for i, line in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        is_title = bool(ARTICLE_RE["en"].match(nxt)) and not line.endswith(TITLE_BAD_END)
        is_chapter = bool(CHAPTER_RE.search(line))
        starts_new = standalone_prev or is_title or is_chapter or PARA_START_RE.match(line)
        if starts_new or not blocks:
            blocks.append(line)
        else:
            blocks[-1] = f"{blocks[-1]} {line}"
        standalone_prev = is_title or is_chapter
    return [fix_pdf_joins(b) for b in blocks]


def fix_pdf_joins(text: str) -> str:
    # pdftotext drops the hyphen of "By-" at a line break.
    return re.sub(r"\bByLaw\b", "By-Law", text)


def extract_blocks(src: Source, cache: Path, refresh: bool) -> list[str]:
    name = re.sub(r"[^A-Za-z0-9]+", "_", src.url)[-80:]
    dest = cache / f"{name}.{'pdf' if src.kind == 'pdf' else 'html'}"
    raw = fetch(src.url, dest, refresh)
    if src.kind == "pdf":
        if not raw.startswith(b"%PDF"):
            raise RuntimeError(f"expected a PDF from {src.url}")
        return blocks_pdf(dest)
    return {"mevzuat": blocks_mevzuat, "kvkk": blocks_kvkk, "rg": blocks_rg}[src.kind](raw)


# --- article segmentation ----------------------------------------------------


@dataclass(frozen=True)
class Article:
    key: str  # e.g. "md-06" or "gecici-01"
    number: int
    provisional: bool
    title: str
    paragraphs: tuple[str, ...]


def split_embedded_titles(blocks: list[str]) -> list[str]:
    """'Some title ARTICLE 12 – (1) ...' -> ['Some title', 'ARTICLE 12 – (1) ...']."""
    out = []
    for b in blocks:
        m = EMBEDDED_ARTICLE_RE.search(b)
        head = b[: m.start()].strip() if m else ""
        if (
            m
            and 0 < len(head) <= 150
            and not head.endswith(TITLE_BAD_END)
            and not re.search(r"(GEÇİCİ|PROVISIONAL)$", head, re.IGNORECASE)
        ):
            out.extend([head, b[m.start() + 1 :]])
        else:
            out.append(b)
    return out


def is_title(block: str) -> bool:
    return (
        len(block) <= 150
        and not block.endswith(TITLE_BAD_END)
        and not PARA_START_RE.match(block)
        and not CHAPTER_RE.search(block)
    )


def strip_chapters(blocks: list[str]) -> list[str]:
    out, skip_next = [], False
    for b in blocks:
        if CHAPTER_RE.search(b):
            skip_next = True  # the chapter's name follows its header
            continue
        if skip_next:
            skip_next = False
            continue
        out.append(b)
    return out


def segment(blocks: list[str], lang: str, warnings: list[str], label: str) -> list[Article]:
    blocks = split_embedded_titles(blocks)
    end = next((i for i, b in enumerate(blocks) if END_RE.match(b)), len(blocks))
    blocks = blocks[:end]
    starts = [i for i, b in enumerate(blocks) if ARTICLE_RE[lang].match(b)]
    articles: list[Article] = []
    last_main = 0
    for n, start in enumerate(starts):
        stop = starts[n + 1] if n + 1 < len(starts) else len(blocks)
        body = strip_chapters(blocks[start:stop])
        if n + 1 < len(starts) and len(body) > 1 and is_title(body[-1]):
            body = body[:-1]  # next article's title
        m = ARTICLE_RE[lang].match(body[0])
        provisional = m.group(1).upper().startswith(("GEÇİCİ", "PROVISIONAL"))
        number = int(m.group(2))
        if not provisional:
            if number != last_main + 1:
                warnings.append(
                    f"{label}: article numbered {number} after {last_main}; renumbered to {last_main + 1}"
                )
                number = last_main + 1
            last_main = number
        first = body[0][m.end() :].strip()
        paragraphs = tuple(p for p in ([first] if first else []) + body[1:] if p)
        prev = blocks[start - 1] if start > 0 else ""
        title = prev if is_title(prev) else ""
        key = f"{'gecici' if provisional else 'md'}-{number:02d}"
        articles.append(Article(key, number, provisional, title, paragraphs))
    return articles


# --- rendering ---------------------------------------------------------------


def render(doc: Document, art: Article, lang: str) -> str:
    if lang == "tr":
        label = f"{'Geçici Madde' if art.provisional else 'Madde'} {art.number}"
        name = doc.name_tr
    else:
        label = f"{'Provisional Article' if art.provisional else 'Article'} {art.number}"
        name = doc.name_en
    heading = f"# {name} — {label}" + (f": {art.title}" if art.title else "")
    return heading + "\n\n" + "\n\n".join(art.paragraphs) + "\n"


def build(cache: Path, refresh: bool) -> int:
    out_dirs = {lang: OUT_ROOT / f"kvkk-{lang}" / "corpus" for lang in ("tr", "en")}
    for d in out_dirs.values():
        if d.exists():
            for f in d.glob("*.md"):
                f.unlink()
        d.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    total = 0
    for doc in DOCUMENTS:
        arts = {}
        for lang in ("tr", "en"):
            src = getattr(doc, lang)
            blocks = extract_blocks(src, cache, refresh)
            arts[lang] = {a.key: a for a in segment(blocks, lang, warnings, f"{doc.prefix}/{lang}")}
        common = sorted(set(arts["tr"]) & set(arts["en"]))
        for lang, other in (("tr", "en"), ("en", "tr")):
            missing = sorted(set(arts[lang]) - set(arts[other]))
            if missing:
                warnings.append(f"{doc.prefix}: only in {lang}, skipped: {missing}")
        for key in common:
            for lang in ("tr", "en"):
                art = arts[lang][key]
                if not art.paragraphs:
                    raise RuntimeError(f"empty article {doc.prefix}-{key} ({lang})")
                path = out_dirs[lang] / f"{doc.prefix}-{key}.md"
                path.write_text(render(doc, art, lang), encoding="utf-8")
        print(f"{doc.prefix}: {len(common)} aligned articles")
        total += len(common)
    for w in warnings:
        print(f"WARNING: {w}")
    print(f"total aligned files per language: {total}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--cache-dir", type=Path, default=Path(tempfile.gettempdir()) / "kvkk-cache")
    parser.add_argument("--refresh", action="store_true", help="re-download cached sources")
    args = parser.parse_args()
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    return build(args.cache_dir, args.refresh)


if __name__ == "__main__":
    sys.exit(main())
