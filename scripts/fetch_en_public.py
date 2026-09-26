#!/usr/bin/env python3
"""Fetch the EN-Public evaluation corpus (PRD §7.1: Wikipedia subset + arXiv abstracts).

Writes:
  eval/datasets/en-public/corpus/wiki-<slug>.md      one file per Wikipedia article
  eval/datasets/en-public/corpus/arxiv-<id>.md       one file per arXiv abstract
  eval/datasets/en-public/SOURCES.md                 provenance + licence attribution
  eval/datasets/en-public/manifest.json              pinned revision ids / arXiv ids

Only corpus documents go into corpus/ because every file there is ingested.

Usage (from the repo root):
  python/.venv/bin/python scripts/fetch_en_public.py [--skip-wiki] [--skip-arxiv]

Stdlib only. Polite by construction: a descriptive User-Agent with no contact
address, ~1 s between Wikipedia requests and >= 3 s between arXiv API requests
(arXiv's stated limit).

Reproducibility note: the MediaWiki TextExtracts API always serves the *current*
revision, so re-running fetches whatever is live that day. The revision id
(oldid) returned in the same request is recorded in manifest.json and SOURCES.md;
it pins exactly which text the frozen corpus was built from.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

USER_AGENT = "AletheiaResearch/0.1 (research eval corpus)"
RETRIEVAL_DATE = "2026-09-26"

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "eval" / "datasets" / "en-public"
CORPUS_DIR = OUT_DIR / "corpus"

WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_SLEEP_S = 1.5
ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_SLEEP_S = 3.5

MAX_ARTICLE_CHARS = 40_000
ARXIV_PER_TOPIC = 24
ARXIV_FETCH_PER_TOPIC = 60  # over-fetch so cross-topic dedupe still leaves 24

# Three clusters of near-duplicate-rich articles. Titles are resolved through
# redirects, so the stored title may differ from the one requested here.
WIKI_CLUSTERS: dict[str, list[str]] = {
    "data-protection": [
        "General Data Protection Regulation",
        "California Consumer Privacy Act",
        "Data Protection Act 2018",
        "ePrivacy Directive",
        "Max Schrems",  # en-wiki has no standalone Schrems II article; "Schrems II" redirects here
        "EU–US Privacy Shield",
        "Health Insurance Portability and Accountability Act",
        "Personal Information Protection and Electronic Documents Act",
        "General Personal Data Protection Law",
        "Personal Information Protection Law of the People's Republic of China",
    ],
    "space-missions": [
        "Voyager 1",
        "Hubble Space Telescope",
        "James Webb Space Telescope",
        "Apollo 11",
        "Cassini–Huygens",
        "Rosetta (spacecraft)",
        "New Horizons",
        "International Space Station",
        "Mars Science Laboratory",
        "Chandrayaan-3",
    ],
    "bridges": [
        "Golden Gate Bridge",
        "Bosphorus Bridge",
        "1915 Çanakkale Bridge",
        "Akashi Kaikyō Bridge",
        "Millau Viaduct",
        "Brooklyn Bridge",
        "Tower Bridge",
        "Øresund Bridge",
        "Sydney Harbour Bridge",
        "Hong Kong–Zhuhai–Macau Bridge",
    ],
}

# Back-matter sections that carry no answerable prose.
DROP_SECTIONS = {
    "see also", "references", "external links", "further reading", "notes",
    "footnotes", "citations", "bibliography", "sources", "notes and references",
    "references and notes", "explanatory notes", "works cited",
}

ARXIV_TOPICS: dict[str, str] = {
    "conformal prediction": 'ti:"conformal prediction" OR abs:"conformal prediction"',
    "retrieval-augmented generation": (
        'ti:"retrieval-augmented generation" OR abs:"retrieval-augmented generation"'
    ),
    "hallucination detection": 'ti:"hallucination detection" OR abs:"hallucination detection"',
    "natural language inference": (
        'ti:"natural language inference" OR abs:"natural language inference"'
    ),
    "selective prediction / abstention": (
        'ti:"selective prediction" OR abs:"selective prediction" '
        'OR ti:"selective classification" OR ti:abstention OR ti:abstain'
    ),
}

ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


# --------------------------------------------------------------------------- http


def http_get(url: str, params: dict[str, str], retries: int = 5) -> bytes:
    full = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(full, headers={"User-Agent": USER_AGENT})
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as err:
            last_err = err
            retry_after = err.headers.get("Retry-After", "") if err.headers else ""
            wait = int(retry_after) if retry_after.isdigit() else 15 * attempt
            print(f"  ! HTTP {err.code} (attempt {attempt}/{retries}); waiting {wait}s",
                  file=sys.stderr)
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError) as err:
            last_err = err
            print(f"  ! request failed (attempt {attempt}/{retries}): {err}", file=sys.stderr)
            time.sleep(10 * attempt)
    raise RuntimeError(f"GET failed after {retries} attempts: {full}") from last_err


# ---------------------------------------------------------------------- wikipedia

_FOLD = str.maketrans({
    "ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "ß": "ss", "ı": "i", "ł": "l",
    "–": "-", "—": "-", "'": "", "’": "",
})


def slugify(title: str) -> str:
    folded = unicodedata.normalize("NFKD", title.translate(_FOLD))
    ascii_only = folded.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_only).strip("-")


@dataclass(frozen=True)
class Section:
    level: int  # 1 = lead, 2 = "==", 3 = "===", ...
    heading: str
    body: str


_HEADING = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$", re.MULTILINE)


def split_sections(text: str) -> list[Section]:
    sections: list[Section] = []
    matches = list(_HEADING.finditer(text))
    lead_end = matches[0].start() if matches else len(text)
    sections.append(Section(1, "", text[:lead_end].strip()))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append(Section(len(m.group(1)), m.group(2).strip(), text[m.end():end].strip()))
    return sections


def drop_back_matter(sections: list[Section]) -> list[Section]:
    kept: list[Section] = []
    dropping_level: int | None = None
    for s in sections:
        if dropping_level is not None and s.level > dropping_level:
            continue  # subsection of a dropped section
        dropping_level = None
        if s.level > 1 and s.heading.lower() in DROP_SECTIONS:
            dropping_level = s.level
            continue
        kept.append(s)
    return kept


def drop_empty(sections: list[Section]) -> list[Section]:
    """Remove headings with no body and no non-empty descendants (e.g. table-only sections)."""
    kept: list[Section] = []
    for i, s in enumerate(sections):
        if s.level == 1 or s.body:
            kept.append(s)
            continue
        has_content_child = False
        for later in sections[i + 1:]:
            if later.level <= s.level:
                break
            if later.body:
                has_content_child = True
                break
        if has_content_child:
            kept.append(s)
    return kept


def render(title: str, sections: list[Section]) -> str:
    parts = [f"# {title}"]
    for s in sections:
        if s.level > 1:
            parts.append(f"{'#' * min(s.level, 4)} {s.heading}")
        if s.body:
            parts.append(s.body)
    return "\n\n".join(parts).strip() + "\n"


def truncate_at_section(title: str, sections: list[Section]) -> tuple[list[Section], bool]:
    if len(render(title, sections)) <= MAX_ARTICLE_CHARS:
        return sections, False
    kept: list[Section] = []
    for s in sections:
        if kept and s.level == 2 and len(render(title, [*kept, s])) > MAX_ARTICLE_CHARS:
            break
        if kept and len(render(title, [*kept, s])) > MAX_ARTICLE_CHARS:
            # A subsection alone overflows: stop at this boundary too.
            break
        kept.append(s)
    return drop_empty(kept), True


def clean_body_text(text: str) -> str:
    text = text.replace(" ", " ")
    text = re.sub(r"\{\\displaystyle[^\n]*?\}\s*", "", text)  # stray math markup
    text = re.sub(r"[ \t]+\n", "\n", text)
    # explaintext separates paragraphs with a single newline; Markdown needs a blank line.
    text = re.sub(r"\n+", "\n\n", text)
    return text.strip()


def fetch_wiki_article(requested: str) -> dict:
    params = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "redirects": "1",
        "prop": "extracts|revisions|info",
        "explaintext": "1",
        "exsectionformat": "wiki",
        "rvprop": "ids|timestamp",
        "inprop": "url",
        "titles": requested,
    }
    data = json.loads(http_get(WIKI_API, params))
    pages = data.get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing"):
        raise LookupError(f"Wikipedia page not found: {requested!r}")
    page = pages[0]
    extract = page.get("extract") or ""
    if not extract.strip():
        raise LookupError(f"Empty extract for {requested!r}")
    rev = page["revisions"][0]
    return {
        "requested": requested,
        "title": page["title"],
        "pageid": page["pageid"],
        "oldid": rev["revid"],
        "rev_timestamp": rev["timestamp"],
        "extract": extract,
    }


def build_wiki(manifest: dict, skipped: list[str]) -> None:
    for cluster, titles in WIKI_CLUSTERS.items():
        for requested in titles:
            try:
                page = fetch_wiki_article(requested)
            except Exception as err:
                skipped.append(f"wiki:{requested} ({err})")
                print(f"  ! skipped {requested}: {err}", file=sys.stderr)
                time.sleep(WIKI_SLEEP_S)
                continue
            sections = [
                Section(s.level, s.heading, clean_body_text(s.body))
                for s in split_sections(page["extract"])
            ]
            sections = drop_empty(drop_back_matter(sections))
            sections, truncated = truncate_at_section(page["title"], sections)
            text = render(page["title"], sections)
            slug = slugify(page["title"])
            filename = f"wiki-{slug}.md"
            (CORPUS_DIR / filename).write_text(text, encoding="utf-8")
            title_q = urllib.parse.quote(page["title"].replace(" ", "_"), safe="")
            manifest["wikipedia"].append({
                "file": filename,
                "cluster": cluster,
                "title": page["title"],
                "requested_title": requested,
                "pageid": page["pageid"],
                "oldid": page["oldid"],
                "revision_timestamp": page["rev_timestamp"],
                "url": f"https://en.wikipedia.org/w/index.php?title={title_q}&oldid={page['oldid']}",
                "chars": len(text),
                "truncated": truncated,
            })
            print(f"  wiki  {filename:60s} {len(text):>6d} chars{' (truncated)' if truncated else ''}")
            time.sleep(WIKI_SLEEP_S)


# -------------------------------------------------------------------------- arXiv


def norm_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def parse_arxiv_feed(raw: bytes) -> list[dict]:
    root = ET.fromstring(raw)
    entries = []
    for e in root.findall("a:entry", ATOM):
        abs_url = e.findtext("a:id", default="", namespaces=ATOM).strip()
        m = re.search(r"arxiv\.org/abs/(.+?)(v\d+)?$", abs_url)
        if not m:
            continue
        authors = [norm_ws(a.findtext("a:name", default="", namespaces=ATOM))
                   for a in e.findall("a:author", ATOM)]
        entries.append({
            "id": m.group(1),
            "version": (m.group(2) or "").lstrip("v"),
            "title": norm_ws(e.findtext("a:title", default="", namespaces=ATOM)),
            "abstract": norm_ws(e.findtext("a:summary", default="", namespaces=ATOM)),
            "authors": [a for a in authors if a],
            "published": e.findtext("a:published", default="", namespaces=ATOM).strip(),
        })
    return entries


def arxiv_filename(arxiv_id: str) -> str:
    return "arxiv-" + re.sub(r"[./]", "-", arxiv_id) + ".md"


def render_arxiv(entry: dict) -> str:
    first = entry["authors"][0] if entry["authors"] else "Unknown"
    suffix = " et al." if len(entry["authors"]) > 1 else ""
    year = entry["published"][:4]
    return (
        f"# {entry['title']}\n\n"
        f"arXiv:{entry['id']} — {first}{suffix}, {year}\n\n"
        f"{entry['abstract']}\n"
    )


def build_arxiv(manifest: dict, skipped: list[str]) -> None:
    seen: set[str] = set()
    for topic, query in ARXIV_TOPICS.items():
        params = {
            "search_query": query,
            "start": "0",
            "max_results": str(ARXIV_FETCH_PER_TOPIC),
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        try:
            entries = parse_arxiv_feed(http_get(ARXIV_API, params))
        except Exception as err:
            skipped.append(f"arxiv-topic:{topic} ({err})")
            print(f"  ! arXiv topic failed {topic}: {err}", file=sys.stderr)
            time.sleep(ARXIV_SLEEP_S)
            continue
        taken = 0
        for entry in entries:
            if taken >= ARXIV_PER_TOPIC:
                break
            if entry["id"] in seen:
                continue
            if not entry["title"] or len(entry["abstract"]) < 200:
                skipped.append(f"arxiv:{entry['id']} (missing title or abstract < 200 chars)")
                continue
            seen.add(entry["id"])
            filename = arxiv_filename(entry["id"])
            text = render_arxiv(entry)
            (CORPUS_DIR / filename).write_text(text, encoding="utf-8")
            manifest["arxiv"].append({
                "file": filename,
                "topic": topic,
                "id": entry["id"],
                "version": entry["version"],
                "title": entry["title"],
                "first_author": entry["authors"][0] if entry["authors"] else "",
                "year": entry["published"][:4],
                "url": f"https://arxiv.org/abs/{entry['id']}"
                       + (f"v{entry['version']}" if entry["version"] else ""),
                "chars": len(text),
            })
            taken += 1
        print(f"  arXiv {topic:40s} {taken} abstracts ({len(entries)} returned)")
        if taken < ARXIV_PER_TOPIC:
            skipped.append(f"arxiv-topic:{topic} only {taken}/{ARXIV_PER_TOPIC} after dedupe")
        time.sleep(ARXIV_SLEEP_S)


# ------------------------------------------------------------------------ outputs


def write_sources(manifest: dict) -> None:
    wiki = manifest["wikipedia"]
    arxiv = manifest["arxiv"]
    lines = [
        "# EN-Public corpus — sources and licences",
        "",
        "PRD §7.1: *Wikipedia subset + arXiv abstracts*, chosen for comparability with",
        "published work. Built by `scripts/fetch_en_public.py`; pinned ids are in",
        "`manifest.json`. Everything under `corpus/` is ingested; this file is not.",
        "",
        f"- Retrieval date: **{RETRIEVAL_DATE}**",
        f"- Wikipedia articles: {len(wiki)} (three clusters of near-duplicate distractors)",
        f"- arXiv abstracts: {len(arxiv)} (five topics, deduplicated across topics)",
        "",
        "## Licences",
        "",
        "**Wikipedia.** Article text is from English Wikipedia contributors and is",
        "licensed under the [Creative Commons Attribution-ShareAlike 4.0 International",
        "Licence (CC BY-SA 4.0)](https://creativecommons.org/licenses/by-sa/4.0/). The",
        "text was **modified**: fetched as plain text through the MediaWiki TextExtracts",
        "API (`prop=extracts&explaintext=1`), which omits tables, infoboxes, images and",
        "citations; converted to Markdown (section headings to `##`/`###`/`####`); back",
        "matter (See also, References, External links, Further reading, Notes and",
        "similar) removed; empty sections removed; articles longer than",
        f"{MAX_ARTICLE_CHARS:,} characters truncated at a section boundary. The derived",
        "files are shared under the same CC BY-SA 4.0 licence. Authorship: see each",
        "article's history page on Wikipedia (the permanent links below identify the",
        "exact revision used).",
        "",
        "**arXiv.** Only titles, author names and abstracts are included — no e-print",
        "(PDF/source) content. Per the [arXiv API Terms of",
        "Use](https://info.arxiv.org/help/api/tou.html), descriptive metadata — which",
        "arXiv defines as including title, abstract, authors, identifiers and",
        "classification terms — is available under the [CC0 1.0 Public Domain",
        "Dedication](https://creativecommons.org/publicdomain/zero/1.0/). Abstract",
        "whitespace was normalised. Thank you to arXiv for use of its open access",
        "interoperability.",
        "",
        "## Wikipedia articles",
        "",
        "| File | Cluster | Article (permanent link) | oldid | Revision timestamp | Chars | Truncated |",
        "|---|---|---|---|---|---|---|",
    ]
    for w in wiki:
        lines.append(
            f"| `{w['file']}` | {w['cluster']} | [{w['title']}]({w['url']}) | {w['oldid']} "
            f"| {w['revision_timestamp']} | {w['chars']} | {'yes' if w['truncated'] else 'no'} |"
        )
    lines += [
        "",
        "## arXiv abstracts",
        "",
        "| File | Topic | Title | First author, year | Source |",
        "|---|---|---|---|---|",
    ]
    for a in arxiv:
        title = a["title"].replace("|", "\\|")
        lines.append(
            f"| `{a['file']}` | {a['topic']} | {title} | {a['first_author']}, {a['year']} "
            f"| [arXiv:{a['id']}]({a['url']}) |"
        )
    if manifest["skipped"]:
        lines += ["", "## Skipped during fetch", ""]
        lines += [f"- {s}" for s in manifest["skipped"]]
    (OUT_DIR / "SOURCES.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--skip-wiki", action="store_true")
    parser.add_argument("--skip-arxiv", action="store_true")
    args = parser.parse_args()

    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT_DIR / "manifest.json"
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    manifest = {
        "retrieval_date": RETRIEVAL_DATE,
        "user_agent": USER_AGENT,
        "wikipedia": [] if not args.skip_wiki else previous.get("wikipedia", []),
        "arxiv": [] if not args.skip_arxiv else previous.get("arxiv", []),
        "skipped": [],
    }
    skipped: list[str] = []
    if not args.skip_wiki:
        for old in CORPUS_DIR.glob("wiki-*.md"):
            old.unlink()
        print("Fetching Wikipedia articles ...")
        build_wiki(manifest, skipped)
    if not args.skip_arxiv:
        for old in CORPUS_DIR.glob("arxiv-*.md"):
            old.unlink()
        print("Fetching arXiv abstracts ...")
        build_arxiv(manifest, skipped)
    manifest["skipped"] = skipped
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    write_sources(manifest)
    print(f"Done: {len(manifest['wikipedia'])} wiki, {len(manifest['arxiv'])} arXiv, "
          f"{len(skipped)} skipped notes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
