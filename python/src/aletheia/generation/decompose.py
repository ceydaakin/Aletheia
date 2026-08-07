"""Claim decomposition: an answer in, atomic claims with citations out.

Granularity is a real decision, not a formatting detail, because it moves G1 and
G2 in opposite directions:

* **Under-splitting** hides a false assertion inside a mostly-true sentence. NLI
  scores the sentence as a whole and can entail it on the strength of the true
  half, so the false half ships. That is a direct threat to the guarantee.
* **Over-splitting** produces fragments with no standalone meaning ("and for
  those signed later"). Nothing entails a fragment, so it is marked unsupported,
  removed or flagged, and the answer rate falls for no real reason.

The rule here: always split on sentence boundaries, and split *within* a sentence
only at an explicit coordinating conjunction where both sides are independently
substantial. That is deliberately conservative about the second kind of split —
a fragment that cannot be verified costs coverage on every query, whereas the
sentences that genuinely need splitting are a minority. Week 6 measures the
trade and can retune :data:`MIN_CLAUSE_CHARS`.

Citations are parsed off the end of each sentence, where citation-constrained
decoding puts them: ``... otuz gündür. [doc_412:v3:chunk_18]``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from aletheia.contracts import DraftClaim

# Trailing citation block: one or more bracketed chunk ids, e.g.
# "[doc_412:v3:chunk_0018]" or "[a:v1:chunk_0001][b:v2:chunk_0003]".
_CITATION_BLOCK = re.compile(r"(?:\s*\[[^\[\]]+\])+\s*$")
_CITATION = re.compile(r"\[([^\[\]]+)\]")

# Tokens that end in a period without ending a sentence. Turkish legal and
# corporate prose is dense with these, and splitting on them shreds the claims.
_ABBREVIATIONS = (
    # Turkish
    "vb", "vs", "bkz", "örn", "md", "bnd", "fıkr", "no", "sn", "dr", "av", "prof",
    # "TL" is deliberately absent: it is a currency, not an abbreviation, and it
    # ends sentences constantly in this register ("... 2.500 TL.").
    "doç", "yrd", "sk", "mah", "cad", "apt", "bkn", "yy", "krş",
    # English
    "e.g", "i.e", "etc", "cf", "vs", "inc", "ltd", "co", "mr", "mrs", "ms", "dr",
    "prof", "fig", "no", "vol", "p", "pp", "art", "sec",
)

# Placeholder for a protected period. Chosen from a private-use codepoint so it
# cannot occur in a real corpus.
_DOT = ""

_ABBREV_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(a) for a in _ABBREVIATIONS) + r")\.",
    re.IGNORECASE,
)
# 2.500 (Turkish thousands separator), 0.05, 1.1.2024
_NUMERIC_DOT = re.compile(r"(?<=\d)\.(?=\d)")
# "1. madde", "3. fıkra" — an ordinal, not a sentence end. Turkish writes
# ordinals this way and the following word is lowercase.
_ORDINAL = re.compile(r"(?<=\b\d)\.(?=\s+[a-zçğıöşü])")

# A sentence is text up to its first terminal punctuation, plus any citation
# block that immediately follows it. Citations sit *after* the period, so a
# plain split on ".\s+" would hand them to the next sentence and leave the one
# that earned them uncited — which the verifier would then call unsupported.
_SENTENCE = re.compile(r".*?[.!?…](?:\s*\[[^\[\]]+\])*", re.DOTALL)

MIN_CLAUSE_CHARS = 25
"""Below this a split clause is treated as a fragment and left attached.

Calibrated against the corpora rather than guessed: "Fesih ihbar süresi otuz
gündür" is 30 characters and is a complete proposition, so a higher bound would
refuse to split sentences that genuinely contain two claims."""

# Coordinating conjunctions that can join two independent clauses. Required to be
# preceded by a comma: without one, "ve" is usually joining noun phrases
# ("fesih ve ihbar"), and splitting there produces nonsense.
_CLAUSE_SPLIT = re.compile(
    r",\s+(?:ve|ancak|fakat|ayrıca|ancak|and|but|however|moreover)\s+",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Sentence:
    text: str
    citations: tuple[str, ...]


def strip_citations(text: str) -> tuple[str, tuple[str, ...]]:
    """Split a sentence into its prose and the chunk ids it cites."""
    match = _CITATION_BLOCK.search(text)
    if not match:
        return text.strip(), ()
    citations = tuple(_CITATION.findall(match.group(0)))
    return text[: match.start()].strip(), citations


def split_sentences(text: str) -> list[str]:
    """Sentence-split without a model.

    Abbreviations, decimals, and Turkish ordinals are masked before splitting and
    restored afterwards, so "2.500 TL" and "1. madde" stay whole.
    """
    if not text.strip():
        return []

    # Mask every period in the match, not just the trailing one: "e.g." has two,
    # and leaving the inner one live splits the sentence at "See e."
    protected = _ABBREV_PATTERN.sub(lambda m: m.group(0).replace(".", _DOT), text)
    protected = _NUMERIC_DOT.sub(_DOT, protected)
    protected = _ORDINAL.sub(_DOT, protected)

    parts: list[str] = []
    end = 0
    for match in _SENTENCE.finditer(protected):
        parts.append(match.group(0).strip())
        end = match.end()

    # A trailing sentence with no terminal punctuation still carries a claim.
    if (remainder := protected[end:].strip()):
        parts.append(remainder)

    return [p.replace(_DOT, ".") for p in parts if p.strip()]


def split_clauses(sentence: str) -> list[str]:
    """Split a sentence at coordinating conjunctions, conservatively.

    Returns the sentence unchanged unless every resulting clause is substantial
    enough to stand on its own — an unverifiable fragment costs answer rate on
    every query it appears in.
    """
    parts = [p.strip() for p in _CLAUSE_SPLIT.split(sentence) if p.strip()]
    if len(parts) < 2:
        return [sentence]
    if any(len(p) < MIN_CLAUSE_CHARS for p in parts):
        return [sentence]
    # Restore terminal punctuation on the leading clauses so each claim reads as
    # a sentence to the verifier.
    return [p if p[-1] in ".!?…" else p + "." for p in parts]


def decompose(answer: str) -> list[DraftClaim]:
    """Turn a generated answer into atomic claims carrying their citations.

    Claims inherit the citations of the sentence they came from: a split clause
    is still supported by whatever the whole sentence cited.
    """
    claims: list[DraftClaim] = []
    for raw in split_sentences(answer):
        prose, citations = strip_citations(raw)
        if not prose:
            continue
        for clause in split_clauses(prose):
            claims.append(DraftClaim(text=clause, citations=list(citations)))
    return claims
