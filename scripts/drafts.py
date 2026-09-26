"""Check and merge drafted evaluation queries.

Drafts are written in fragments — one file per drafting pass — so that several
passes can run in parallel without editing the same file. Each fragment is a
JSON list of items:

    {
      "id": "kvkk-a-001",
      "category": "answerable" | "multi-doc" | "unanswerable",
      "relevant_docs": ["6698-md-11.md"],          # [] for unanswerable
      "note": "why this is a good/hard query",
      "tr": {"text": "...", "answer": "...", "evidence": ["verbatim quote", ...]},
      "en": {"text": "...", "answer": "...", "evidence": ["verbatim quote", ...]}
    }

Monolingual datasets (en-public) use only the "en" key.

    python scripts/drafts.py check  eval/datasets/kvkk-tr eval/datasets/kvkk-en  frag.json
    python scripts/drafts.py merge  eval/datasets/kvkk-tr eval/datasets/kvkk-en  frag1.json frag2.json ...

``check`` is what a drafting pass runs on its own output before handing it in.
``merge`` rewrites each dataset's dataset.json from all fragments; every query
it writes is ``status: draft`` until a person verifies it with
``python -m aletheia.eval.review``.

The checks are exactly the ones a machine can do. They catch fabricated or
paraphrased evidence and dangling labels; they cannot tell whether a question is
natural, whether the answer is right, or whether an "unanswerable" query really
is — that is what human verification is for.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CATEGORIES = ("answerable", "multi-doc", "unanswerable")
LANG_KEYS = ("tr", "en")


def normalise(text: str) -> str:
    return " ".join(text.split()).casefold()


def _dataset_lang(root: Path) -> str:
    manifest = root / "dataset.json"
    if manifest.exists():
        return json.loads(manifest.read_text(encoding="utf-8")).get("lang", "")
    return "tr" if root.name.endswith("-tr") else "en"


def check(roots: list[Path], items: list[dict]) -> list[str]:
    errors: list[str] = []
    langs = {root: _dataset_lang(root) for root in roots}
    texts = {
        root: {
            p.name: normalise(p.read_text(encoding="utf-8"))
            for p in (root / "corpus").glob("*") if p.is_file()
        }
        for root in roots
    }
    seen: set[str] = set()
    for item in items:
        ident = item.get("id", "?")
        if ident in seen:
            errors.append(f"{ident}: duplicate id")
        seen.add(ident)
        category = item.get("category")
        if category not in CATEGORIES:
            errors.append(f"{ident}: category {category!r} not in {CATEGORIES}")
        docs = item.get("relevant_docs", [])
        if category == "unanswerable" and docs:
            errors.append(f"{ident}: unanswerable query must have no relevant_docs")
        if category != "unanswerable" and not docs:
            errors.append(f"{ident}: answerable query needs relevant_docs")
        if category == "multi-doc" and len(docs) < 2:
            errors.append(f"{ident}: multi-doc query needs at least two relevant_docs")

        for root in roots:
            lang = langs[root]
            body = item.get(lang)
            if not isinstance(body, dict) or not body.get("text", "").strip():
                errors.append(f"{ident}: missing {lang!r} text")
                continue
            for doc in docs:
                if doc not in texts[root]:
                    errors.append(f"{ident}: {doc} not in {root.name}/corpus")
            evidence = body.get("evidence", [])
            if category == "unanswerable":
                if evidence:
                    errors.append(f"{ident}/{lang}: unanswerable query must have no evidence")
                continue
            if not evidence:
                errors.append(f"{ident}/{lang}: no evidence quotes")
            if not body.get("answer", "").strip():
                errors.append(f"{ident}/{lang}: no answer")
            for quote in evidence:
                if len(quote.strip()) < 15:
                    errors.append(f"{ident}/{lang}: evidence quote too short: {quote!r}")
                if not any(normalise(quote) in texts[root].get(doc, "") for doc in docs):
                    errors.append(f"{ident}/{lang}: evidence not verbatim in relevant_docs: {quote[:70]!r}")
    return errors


def merge(roots: list[Path], items: list[dict]) -> None:
    for root in roots:
        lang = _dataset_lang(root)
        manifest_path = root / "dataset.json"
        manifest = (
            json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_path.exists()
            else {"name": root.name, "lang": lang, "description": ""}
        )
        # Keep human decisions: a query already verified or rejected keeps its
        # status and text through a re-merge, so re-drafting never undoes review.
        existing = {q["id"]: q for q in manifest.get("queries", [])}
        queries = []
        for item in items:
            body = item[lang]
            prior = existing.get(item["id"])
            if prior and prior.get("status") in ("verified", "rejected"):
                queries.append(prior)
                continue
            queries.append(
                {
                    "id": item["id"],
                    "text": body["text"],
                    "category": item["category"],
                    "relevant_docs": item.get("relevant_docs", []),
                    "evidence": body.get("evidence", []),
                    "answer": body.get("answer", ""),
                    "note": item.get("note", ""),
                    "status": "draft",
                }
            )
        manifest["queries"] = queries
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        counts = {c: sum(q["category"] == c for q in queries) for c in CATEGORIES}
        print(f"{manifest_path}: {len(queries)} queries {counts}")


def _load(paths: list[Path]) -> list[dict]:
    items: list[dict] = []
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise SystemExit(f"{path}: expected a JSON list of items")
        items.extend(data)
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("check", "merge"))
    parser.add_argument("paths", nargs="+", type=Path, help="dataset dirs, then fragment files")
    args = parser.parse_args(argv)

    roots = [p for p in args.paths if p.is_dir()]
    fragments = [p for p in args.paths if p.is_file()]
    if not roots or not fragments:
        parser.error("give at least one dataset directory and one fragment file")

    items = _load(fragments)
    errors = check(roots, items)
    if errors:
        print(f"{len(errors)} problem(s):", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    print(f"ok: {len(items)} items, " + ", ".join(
        f"{c}={sum(i.get('category') == c for i in items)}" for c in CATEGORIES))
    if args.command == "merge":
        merge(roots, items)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
