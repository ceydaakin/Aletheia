"""Human verification of drafted queries — the step with no shortcut (PRD §7.2).

    python -m aletheia.eval.review --dataset ../eval/datasets/kvkk-tr \\
        --parallel ../eval/datasets/kvkk-en
    python -m aletheia.eval.review --dataset ../eval/datasets/en-public --category unanswerable

Walks every ``draft`` query, shows the question, the drafted answer, each
evidence quote *in its surrounding document text*, and the drafter's note, and
records a decision:

    v  verified — the question is natural, the answer is right, the evidence
       proves it (for unanswerable: the corpus really does not answer it)
    r  rejected — dropped from every run (kept in the file as evidence of review)
    e  edit the question text, then decide
    s  skip for now        q  save and quit

With ``--parallel`` the same id is shown in both languages and one decision
applies to both, because a parallel pair is only useful if both halves are good.
Every decision is written to disk immediately; quitting loses nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from aletheia.eval.dataset import DRAFT, REJECTED, VERIFIED, normalise

CONTEXT_CHARS = 220


def read_manifest(root: Path) -> dict:
    return json.loads((root / "dataset.json").read_text(encoding="utf-8"))


def write_manifest(root: Path, manifest: dict) -> None:
    """Atomic: a crash mid-write must not corrupt a half-reviewed set."""
    target = root / "dataset.json"
    handle, tmp = tempfile.mkstemp(dir=root, prefix=".dataset.", suffix=".json")
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        out.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, target)


def with_decision(manifest: dict, query_id: str, status: str, *, text: str | None = None) -> dict:
    """Return a new manifest with one query's status (and optionally text) set."""
    if status not in (VERIFIED, REJECTED, DRAFT):
        raise ValueError(f"unknown status {status!r}")
    found = False
    queries = []
    for query in manifest["queries"]:
        if query["id"] == query_id:
            found = True
            query = {**query, "status": status, **({"text": text} if text else {})}
        queries.append(query)
    if not found:
        raise KeyError(query_id)
    return {**manifest, "queries": queries}


def context(document: str, quote: str, width: int = CONTEXT_CHARS) -> str:
    """The quote in its surroundings, marked with »…«, whitespace collapsed."""
    flat = " ".join(document.split())
    start = normalise(flat).find(normalise(quote))
    if start < 0:
        return f"[quote not found in document] {quote}"
    end = start + len(" ".join(quote.split()))
    return (
        ("…" if start > width else "") + flat[max(0, start - width):start]
        + "»" + flat[start:end] + "«"
        + flat[end:end + width] + ("…" if end + width < len(flat) else "")
    )


def render(root: Path, query: dict) -> str:
    lines = [f"  Q: {query['text']}"]
    if query.get("answer"):
        lines.append(f"  A: {query['answer']}")
    for quote in query.get("evidence", []):
        for doc in query.get("relevant_docs", []):
            path = root / "corpus" / doc
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            if normalise(quote) in normalise(text):
                lines.append(f"  [{doc}] {context(text, quote)}")
                break
    return "\n".join(lines)


def _pending(manifest: dict, category: str) -> list[dict]:
    return [
        q for q in manifest["queries"]
        if q.get("status", VERIFIED) == DRAFT and (not category or q.get("category") == category)
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aletheia-review", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--parallel", type=Path, help="dataset with the same ids in another language")
    parser.add_argument("--category", default="", help="only review this category")
    args = parser.parse_args(argv)

    roots = [args.dataset] + ([args.parallel] if args.parallel else [])
    manifests = [read_manifest(root) for root in roots]
    by_id = [{q["id"]: q for q in m["queries"]} for m in manifests]
    todo = _pending(manifests[0], args.category)
    total = len(manifests[0]["queries"])
    print(f"{len(todo)} draft queries to review ({total} in the set). v/r/e/s/q")

    for index, query in enumerate(todo, start=1):
        print(f"\n[{index}/{len(todo)}] {query['id']}  ({query.get('category', '')})  "
              f"docs={', '.join(query.get('relevant_docs', [])) or '—'}")
        for root, lookup in zip(roots, by_id, strict=True):
            print(f" {root.name}:")
            print(render(root, lookup.get(query["id"], query)))
        if query.get("note"):
            print(f"  note: {query['note']}")

        while True:
            choice = input("  > ").strip().lower()
            if choice in ("v", "r"):
                status = VERIFIED if choice == "v" else REJECTED
                for i, root in enumerate(roots):
                    if query["id"] in by_id[i]:
                        manifests[i] = with_decision(manifests[i], query["id"], status)
                        write_manifest(root, manifests[i])
                break
            if choice == "e":
                for i, root in enumerate(roots):
                    if query["id"] not in by_id[i]:
                        continue
                    new = input(f"  new question ({root.name}, blank keeps): ").strip()
                    if new:
                        manifests[i] = with_decision(manifests[i], query["id"], DRAFT, text=new)
                        write_manifest(root, manifests[i])
                print("  edited; now v or r")
                continue
            if choice == "s":
                break
            if choice == "q":
                print("saved.")
                return 0
            print("  v=verify r=reject e=edit s=skip q=quit")
    print("\nreview complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
