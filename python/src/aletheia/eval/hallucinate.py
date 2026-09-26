"""Controlled hallucination: claims corrupted on purpose, with the truth recorded.

Why this exists. The default generator is extractive, so every claim it emits is
copied from its evidence and none can be unsupported. A bound calibrated against
it measures nothing about hallucination (README, report §7.2). And the calibration
labels used to come from the verifier itself, so the bound was conditional on the
verifier being right (report §7.3). Both problems have the same fix: responses in
which some claims are *known* to be false, and a loss read from that knowledge
rather than from the component under test.

That is what this module produces. It takes the claims a generator emitted and,
at a chosen rate, rewrites some of them in the ways generative models actually
go wrong in retrieval-augmented answers:

* **number** — a figure, a duration, a year changed to a nearby plausible value
  ("30 days" → "60 days", "1977" → "1979"). The most damaging error in the
  regulated-domain persona (PRD §3) and the one the thesis rests on (§5.2).
* **negation** — polarity flipped ("shall" → "shall not", "silinir" → "silinmez").
* **term** — a domain entity swapped for the one it is most often confused with
  ("data controller" → "data processor", "yurt içi" → "yurt dışı").

The corrupted claim keeps its citations. It still points at the passage that
contradicts it, which is what a real hallucination does: the model cites the
right source and misreports it.

What this does **not** model, and the report says so: fabricated content with no
counterpart in the evidence, subtle paraphrase drift, and errors of omission. The
bound calibrated here is a bound on *these* error types at *this* rate; it is a
controlled experiment, not a measurement of any particular LLM.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from aletheia.contracts import DraftClaim

KINDS = ("number", "negation", "term")
LANGS = ("tr", "en")


@dataclass(frozen=True)
class Corruption:
    kind: str
    original: str
    corrupted: str


@dataclass(frozen=True)
class ClaimLabel:
    """Ground truth for one claim. ``corrupted`` is the loss's input."""

    corrupted: bool
    kind: str = ""
    original: str = ""


@dataclass(frozen=True)
class Injection:
    claims: list[DraftClaim]
    labels: list[ClaimLabel]


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------

_TR_UNITS = ("", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz")
_TR_TENS = ("", "on", "yirmi", "otuz", "kırk", "elli", "altmış", "yetmiş", "seksen", "doksan")
_EN_UNITS = (
    "", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
    "eighteen", "nineteen",
)
_EN_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")

# A digit run, optionally grouped by thousands separators ("1,280", "2.500").
_NUMBER = re.compile(r"(?<![\w.,])(\d{1,3}(?:[.,]\d{3})+|\d+)(?![\w])")
_YEAR = re.compile(r"^(1[5-9]\d\d|20\d\d)$")
# A leading enumerator — "(2) ", "3) ", "4. " — is a paragraph label, not a
# fact. Renumbering it leaves the provision true, so it must never be the
# number that gets corrupted.
_ENUMERATOR = re.compile(r"^\s*\(?\d+[).]\s")
# Editorial annotations and bracketed identifiers carry numbers that are
# bookkeeping, not content: "(Değişik:2/3/2024-7499/33 md.)", "[sicil-md-07]".
_NOT_CONTENT = re.compile(
    r"\((?:Değişik|Ek|Mülga|İptal|Amended|Added|Repealed|Annulled)[^)]*\)|\[[^\]]*\]?",
    re.IGNORECASE,
)


def _words(n: int, lang: str) -> str:
    """Spell 1–99 the way statutes do next to the digits: "otuz (30)"."""
    if lang == "tr":
        return " ".join(w for w in (_TR_TENS[n // 10], _TR_UNITS[n % 10]) if w)
    if n < 20:
        return _EN_UNITS[n]
    tens, units = _EN_TENS[n // 10], _EN_UNITS[n % 10]
    return f"{tens}-{units}" if units else tens


_MONTH_BEFORE = re.compile(
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December"
    r"|Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|Temmuz|Ağustos|Eylül|Ekim|Kasım|Aralık)\s+$"
)
_MONTH_AFTER = re.compile(
    r"^\s+(?:January|February|March|April|May|June|July|August|September|October|November|December"
    r"|Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|Temmuz|Ağustos|Eylül|Ekim|Kasım|Aralık)"
)


def _replacement_number(value: int, rng: random.Random, *, year: bool, day: bool = False) -> int:
    if day:
        # A day of the month must stay a day: "June 56" is not a plausible
        # hallucination, it is noise any reader rejects.
        return rng.choice([d for d in range(max(1, value - 9), min(28, value + 9) + 1) if d != value])
    if year:
        return value + rng.choice([d for d in range(-10, 11) if d != 0])
    options = {value * 2, value * 3, max(1, value // 2)}
    options.add(value + (1 if value < 10 else 10 ** (len(str(value)) - 1)))
    options.discard(value)
    return rng.choice(sorted(options))


def _format_like(original: str, value: int) -> str:
    """Keep the thousands separator the source used, so only the value changes."""
    separator = "," if "," in original else "." if "." in original else ""
    text = str(value)
    if not separator or len(text) <= 3:
        return text
    groups = []
    while text:
        groups.insert(0, text[-3:])
        text = text[:-3]
    return separator.join(groups)


def _spelled_numbers(lang: str) -> re.Pattern[str]:
    """Number words 2–99. "bir"/"one" are excluded: they are articles far more
    often than quantities, and changing "a notice" to "three notices" corrupts
    grammar, not meaning."""
    words = sorted((_words(n, lang) for n in range(2, 100)), key=len, reverse=True)
    return re.compile(
        r"\b(" + "|".join(re.escape(w) for w in words) + r")\b(?!\s*\(\s*\d)(?!-\w)", re.IGNORECASE
    )


_SPELLED = {lang: _spelled_numbers(lang) for lang in LANGS}
_SPELLED_VALUE = {lang: {_words(n, lang): n for n in range(2, 100)} for lang in LANGS}


def _corrupt_spelled(sentence: str, lang: str, match: re.Match[str], rng: random.Random) -> str:
    value = _SPELLED_VALUE[lang][match.group(1).lower()]
    options = [
        n for n in {value * 2, value * 3, value // 2, value + 1, value + 10}
        if 2 <= n < 100 and n != value
    ]
    new = _words(rng.choice(sorted(options)), lang)
    new = _case_like(match.group(1), new)
    return sentence[: match.start(1)] + new + sentence[match.end(1):]


def _corrupt_number(sentence: str, lang: str, rng: random.Random) -> str | None:
    label = _ENUMERATOR.match(sentence)
    excluded = [(0, label.end())] if label else []
    excluded += [m.span() for m in _NOT_CONTENT.finditer(sentence)]
    digit_matches = [
        m for m in _NUMBER.finditer(sentence)
        if not any(start <= m.start() < end for start, end in excluded)
    ]
    spelled_matches = list(_SPELLED[lang].finditer(sentence))
    if not digit_matches and not spelled_matches:
        return None
    pick = rng.randrange(len(digit_matches) + len(spelled_matches))
    if pick >= len(digit_matches):
        return _corrupt_spelled(sentence, lang, spelled_matches[pick - len(digit_matches)], rng)
    match = digit_matches[pick]
    raw = match.group(1)
    value = int(re.sub(r"[.,]", "", raw))
    start, end = match.span(1)
    is_day = 1 <= value <= 31 and bool(
        _MONTH_BEFORE.search(sentence[:start]) or _MONTH_AFTER.match(sentence[end:])
    )
    new = _replacement_number(value, rng, year=bool(_YEAR.match(raw)), day=is_day)
    corrupted = sentence[:start] + _format_like(raw, new) + sentence[end:]

    # "otuz (30)": change the spelled-out number too, or the claim contradicts
    # itself and any verifier catches it for the wrong reason.
    if 0 < value < 100 and 0 < new < 100:
        spelled = _words(value, lang)
        pattern = re.compile(rf"\b{re.escape(spelled)}(\s*\(\s*){re.escape(_format_like(raw, new))}\b", re.I)
        corrupted = pattern.sub(lambda m: _words(new, lang) + m.group(1) + _format_like(raw, new), corrupted)
    return corrupted


# ---------------------------------------------------------------------------
# Negation
# ---------------------------------------------------------------------------

Rule = tuple[re.Pattern[str], Callable[[re.Match[str]], str | None]]


def _rule(
    pattern: str,
    replacement: str | Callable[[re.Match[str]], str],
    *,
    flags: int = re.IGNORECASE,
) -> Rule:
    compiled = re.compile(pattern, flags)
    if isinstance(replacement, str):
        template = replacement
        return compiled, lambda m: m.expand(template)
    return compiled, replacement


# Ordered: the first rule that matches wins. Removing an existing negation comes
# before adding one, so "cannot" becomes "can" rather than "cannot not".
# Case-sensitive on purpose: auxiliaries inside a claim are lowercase, and
# "May 27" is a date, not a modal.
_EN_NEGATION: tuple[Rule, ...] = tuple(
    _rule(pattern, replacement, flags=0)
    for pattern, replacement in (
        (r"\bcannot\b", "can"),
        (r"\b(shall|may|must|will|should|can|could|would) not\b", r"\1"),
        (r"\b(is|are|was|were|has|have|had|does|did|do) not\b", r"\1"),
        (r"\b(shall|may|must|will|should|could|would)\b", r"\1 not"),
        (r"\bcan\b", "cannot"),
        (r"\b(is|are|was|were)\b", r"\1 not"),
        (r"\b(has|have|had) (been|\w+ed)\b", r"\1 not \2"),
    )
)


def _tr_aorist(match: re.Match[str]) -> str:
    """Negate a passive/aorist verb by vowel harmony: silinir → silinmez."""
    stem, vowel = match.group(1), match.group(2)
    return stem + ("maz" if vowel.lower() in "ıu" else "mez")


_TR_VOWELS = set("aeıioöuüAEIİOÖUÜ")
_TR_VOICELESS = set("çfhkpsştÇFHKPSŞT")


def _tr_copula(match: re.Match[str]) -> str | None:
    """Negate a nominal predicate: sorumludur → sorumlu değildir.

    Only when the suffix's first consonant agrees with the stem the way the
    copula's does — d after a vowel or voiced consonant, t after a voiceless
    one. "yürütür" (yürüt- + aorist) fails that test and is left alone: turning
    it into "yürü değildir" garbles the sentence rather than falsifying it.
    """
    stem, suffix = match.group(1), match.group(2)
    last = stem[-1]
    expected = "t" if last in _TR_VOICELESS else "d"
    if suffix[0] != expected:
        return None
    return stem + " değildir"


# Turkish negation is a suffix, so the rules are morphological. Deliberately
# narrow: every pattern needs enough of a verb ending in front of it that a noun
# like "bir" or "gelir" does not qualify by accident — a garbled claim is easy
# to reject for the wrong reason, which would flatter the verifier.
_TR_NEGATION: tuple[Rule, ...] = (
    _rule(r"\bzorunda değildir\b", "zorundadır"),
    _rule(r"\bzorundadır\b", "zorunda değildir"),
    _rule(r"\bgerekmemektedir\b", "gerekmektedir"),
    _rule(r"\bgerekmektedir\b", "gerekmemektedir"),
    _rule(r"\bgerekmez\b", "gerekir"),
    _rule(r"\bgerekir\b", "gerekmez"),
    _rule(r"\bvardır\b", "yoktur"),
    _rule(r"\byoktur\b", "vardır"),
    _rule(r"\byasaktır\b", "serbesttir"),
    _rule(r"\b(\w{2,})ebilir(ler)?\b", lambda m: m.group(1) + "emez" + (m.group(2) or "")),
    _rule(r"\b(\w{2,})abilir(ler)?\b", lambda m: m.group(1) + "amaz" + (m.group(2) or "")),
    _rule(r"\b(\w{3,}[nl])([ıiuü])r\b", _tr_aorist),
    _rule(r"\b(\w{3,}?)(dır|dir|dur|dür|tır|tir|tur|tür)\b", lambda m: _tr_copula(m)),
)


def _apply_first(sentence: str, rules: Sequence[Rule]) -> str | None:
    """First rule, first match, whose replacement applies. A replacement may
    return None to decline a match that only looks like its pattern."""
    for pattern, replace in rules:
        for match in pattern.finditer(sentence):
            replacement = replace(match)
            if replacement is not None:
                return sentence[: match.start()] + replacement + sentence[match.end():]
    return None


def _corrupt_negation(sentence: str, lang: str, rng: random.Random) -> str | None:
    return _apply_first(sentence, _TR_NEGATION if lang == "tr" else _EN_NEGATION)


# ---------------------------------------------------------------------------
# Term swaps
# ---------------------------------------------------------------------------

# Pairs a model plausibly confuses, applied in both directions. Chosen for the
# corpora in eval/datasets: data-protection law in both languages, space
# missions and bridges in English.
_EN_TERMS = (
    ("data controller", "data processor"),
    ("explicit consent", "implied consent"),
    ("the Board", "the President"),
    ("minimum", "maximum"),
    ("at least", "at most"),
    ("before", "after"),
    ("first", "last"),
    ("longest", "shortest"),
    ("largest", "smallest"),
    ("north", "south"),
    ("east", "west"),
    ("increase", "decrease"),
    ("higher", "lower"),
    ("days", "months"),
    ("months", "years"),
)
_TR_TERMS = (
    ("veri sorumlusu", "veri işleyen"),
    ("açık rıza", "aydınlatma"),
    ("Kurul", "Başkan"),
    ("yurt içi", "yurt dışı"),
    ("yurt dışına", "yurt içine"),
    ("en az", "en çok"),
    ("asgari", "azami"),
    ("önceden", "sonradan"),
    ("önce", "sonra"),
    ("gün", "ay"),
    ("ay", "yıl"),
)


def _case_like(source: str, target: str) -> str:
    return target[0].upper() + target[1:] if source[:1].isupper() else target


def _corrupt_term(sentence: str, lang: str, rng: random.Random) -> str | None:
    pairs = _TR_TERMS if lang == "tr" else _EN_TERMS
    swaps = [*pairs, *((b, a) for a, b in pairs)]
    hits = []
    for source, target in swaps:
        for match in re.finditer(rf"\b{re.escape(source)}\b", sentence, re.IGNORECASE):
            hits.append((match.start(), match.end(), target))
    if not hits:
        return None
    start, end, target = rng.choice(sorted(hits))
    return sentence[:start] + _case_like(sentence[start:end], target) + sentence[end:]


_CORRUPTERS: dict[str, Callable[[str, str, random.Random], str | None]] = {
    "number": _corrupt_number,
    "negation": _corrupt_negation,
    "term": _corrupt_term,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def corrupt(
    sentence: str,
    lang: str,
    rng: random.Random,
    *,
    kinds: Sequence[str] = KINDS,
) -> Corruption | None:
    """Rewrite one claim so that its evidence no longer supports it.

    Returns ``None`` when no rule applies — the caller must then treat the claim
    as clean. Labelling an unchanged claim as corrupted would put a true
    statement in the loss and make the verifier look worse than it is.
    """
    lang = lang[:2].lower()
    if lang not in LANGS:
        raise ValueError(f"no corruption rules for language {lang!r}")

    # Try every requested kind so the choice is among those that apply; a
    # sentence with no number should still be corruptible by negation.
    applicable: list[tuple[str, str]] = []
    for kind in kinds:
        # A child generator per kind keeps each kind's draws independent of
        # which other kinds were tried, so adding a kind does not reshuffle the
        # rest of the experiment.
        candidate = _CORRUPTERS[kind](sentence, lang, random.Random(rng.random()))
        if candidate is not None and candidate != sentence:
            applicable.append((kind, candidate))
    if not applicable:
        return None
    kind, corrupted = rng.choice(applicable)
    return Corruption(kind=kind, original=sentence, corrupted=corrupted)


def inject(
    claims: Sequence[DraftClaim],
    *,
    lang: str,
    rate: float,
    rng: random.Random,
    kinds: Sequence[str] = KINDS,
) -> Injection:
    """Corrupt each claim independently with probability ``rate``.

    Returns new claims; the input is never modified. One draw is taken per claim
    whether or not it is corrupted, so the random stream — and therefore which
    claims are hit — does not depend on the claims' content.
    """
    if not 0.0 <= rate <= 1.0:
        raise ValueError("rate must lie in [0, 1]")

    out_claims: list[DraftClaim] = []
    labels: list[ClaimLabel] = []
    for claim in claims:
        hit = rng.random() < rate
        seed = rng.random()
        corruption = corrupt(claim.text, lang, random.Random(seed), kinds=kinds) if hit else None
        if corruption is None:
            out_claims.append(claim.model_copy())
            labels.append(ClaimLabel(corrupted=False))
        else:
            out_claims.append(claim.model_copy(update={"text": corruption.corrupted}))
            labels.append(
                ClaimLabel(corrupted=True, kind=corruption.kind, original=corruption.original)
            )
    return Injection(claims=out_claims, labels=labels)
