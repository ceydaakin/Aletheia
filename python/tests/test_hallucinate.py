"""Controlled hallucination: the ground truth the calibration loss is built on.

Every test here guards one property the loss depends on: a corrupted claim must
actually say something different (or it would be labelled unsupported while
still being true), an untouched claim must be byte-identical to its evidence
(or "clean" would not mean supported), and the whole thing must be reproducible
from a seed (PRD G6).
"""

from __future__ import annotations

import random

import pytest

from aletheia.contracts import DraftClaim
from aletheia.eval.hallucinate import corrupt, inject


def _rng(seed: int = 0) -> random.Random:
    return random.Random(seed)


# --- corrupt(): one sentence ------------------------------------------------


@pytest.mark.parametrize(
    ("sentence", "lang"),
    [
        ("Taraflar otuz (30) gün önceden yazılı ihbarda bulunur.", "tr"),
        ("The bridge opened in 1937 after four years of construction.", "en"),
        ("The span is 1,280 metres long.", "en"),
        ("Başvuru en geç 30 gün içinde sonuçlandırılır.", "tr"),
    ],
)
def test_corruption_changes_the_text(sentence: str, lang: str) -> None:
    result = corrupt(sentence, lang, _rng())
    assert result is not None
    assert result.corrupted != sentence
    assert result.original == sentence


def test_number_corruption_never_maps_a_number_to_itself() -> None:
    for seed in range(200):
        result = corrupt("The fine is 50 euros.", "en", _rng(seed), kinds=("number",))
        assert result is not None
        assert result.corrupted != "The fine is 50 euros."
        assert " 50 euros" not in result.corrupted


def test_year_corruption_stays_plausible() -> None:
    """A year shifted by millennia is caught by any reader; a year shifted by a
    few is the hallucination that actually ships."""
    for seed in range(100):
        result = corrupt("It was launched in 1977.", "en", _rng(seed))
        assert result is not None
        if result.kind == "number":
            year = int(result.corrupted.split()[-1].rstrip("."))
            assert 1967 <= year <= 1987
            assert year != 1977


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        ("Personal data shall be erased.", "shall not"),
        ("The controller may transfer the data.", "may not"),
        ("Processing is prohibited.", "is not"),
        ("The Board cannot impose a fine.", "can impose"),
    ],
)
def test_english_negation(sentence: str, expected: str) -> None:
    result = corrupt(sentence, "en", _rng(), kinds=("negation",))
    assert result is not None
    assert expected in result.corrupted


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        ("Kişisel veriler silinir.", "silinmez"),
        ("Veriler yurt dışına aktarılabilir.", "aktarılamaz"),
        ("Veri sorumlusu bildirmekle yükümlüdür.", "yükümlü değildir"),
        ("Açık rıza alınması gerekir.", "gerekmez"),
        ("Bu durumda istisna vardır.", "yoktur"),
    ],
)
def test_turkish_negation(sentence: str, expected: str) -> None:
    result = corrupt(sentence, "tr", _rng(), kinds=("negation",))
    assert result is not None
    assert expected in result.corrupted


def test_turkish_negation_leaves_non_verbs_alone() -> None:
    """'bir' ends in -ir and is not a verb; negating it would corrupt the
    grammar rather than the meaning, and a garbled claim is easy for any
    verifier to reject — which would flatter it."""
    result = corrupt("Bir kişi.", "tr", _rng(), kinds=("negation",))
    assert result is None


def test_term_swap_uses_the_domain_confusion() -> None:
    result = corrupt(
        "The data controller must notify the Board.", "en", _rng(), kinds=("term",)
    )
    assert result is not None
    assert "data processor" in result.corrupted


def test_returns_none_when_nothing_applies() -> None:
    assert corrupt("Hello.", "en", _rng()) is None


def test_corruption_is_deterministic_for_a_seed() -> None:
    sentence = "Voyager 1 was launched on 5 September 1977 and is the most distant probe."
    first = corrupt(sentence, "en", _rng(7))
    second = corrupt(sentence, "en", _rng(7))
    assert first == second


def test_unknown_language_is_rejected() -> None:
    with pytest.raises(ValueError):
        corrupt("x", "de", _rng())


# --- inject(): a whole answer ----------------------------------------------


def _claims(n: int) -> list[DraftClaim]:
    return [
        DraftClaim(text=f"Clause {i} requires notice within {10 + i} days.", citations=[f"c{i}"])
        for i in range(n)
    ]


def test_inject_labels_exactly_the_changed_claims() -> None:
    claims = _claims(40)
    result = inject(claims, lang="en", rate=0.5, rng=_rng(3))
    assert len(result.claims) == len(claims)
    for before, after, label in zip(claims, result.claims, result.labels, strict=True):
        if label.corrupted:
            assert after.text != before.text
        else:
            assert after.text == before.text
        # Citations are never touched: the corrupted claim still points at the
        # evidence that contradicts it, which is what a real hallucination does.
        assert after.citations == before.citations


def test_inject_rate_zero_changes_nothing() -> None:
    claims = _claims(10)
    result = inject(claims, lang="en", rate=0.0, rng=_rng())
    assert [c.text for c in result.claims] == [c.text for c in claims]
    assert not any(label.corrupted for label in result.labels)


def test_inject_rate_one_corrupts_everything_corruptible() -> None:
    claims = [*_claims(10), DraftClaim(text="Hello.", citations=["x"])]
    result = inject(claims, lang="en", rate=1.0, rng=_rng())
    assert all(label.corrupted for label in result.labels[:10])
    # Nothing applies to the last one, and it must not be labelled as corrupted.
    assert not result.labels[10].corrupted


def test_inject_does_not_mutate_its_input() -> None:
    claims = _claims(5)
    snapshot = [c.model_copy() for c in claims]
    inject(claims, lang="en", rate=1.0, rng=_rng())
    assert claims == snapshot


def test_inject_rate_is_roughly_honoured() -> None:
    claims = _claims(2000)
    result = inject(claims, lang="en", rate=0.2, rng=_rng(11))
    share = sum(label.corrupted for label in result.labels) / len(claims)
    assert 0.17 < share < 0.23


def test_inject_rejects_invalid_rate() -> None:
    with pytest.raises(ValueError):
        inject(_claims(1), lang="en", rate=1.5, rng=_rng())


def test_spelled_out_numbers_are_corruptible() -> None:
    """Statutes often write "otuz gün" with no digits at all."""
    result = corrupt("Başvuru otuz gün içinde sonuçlandırılır.", "tr", _rng(), kinds=("number",))
    assert result is not None
    assert "otuz gün" not in result.corrupted


def test_spelled_number_next_to_digits_changes_together() -> None:
    for seed in range(50):
        result = corrupt("Notice of thirty (30) days is required.", "en", _rng(seed), kinds=("number",))
        assert result is not None
        assert "thirty (30)" not in result.corrupted
        # Never half-changed: word and digits must still agree.
        assert "thirty (" not in result.corrupted
        assert "(30)" not in result.corrupted


def test_a_month_name_is_not_a_modal() -> None:
    result = corrupt("It opened on May 27, 1937.", "en", _rng(), kinds=("negation",))
    assert result is None


@pytest.mark.parametrize(
    "sentence",
    [
        "(2) Kişisel veriler ilgili kişinin açık rızası olmaksızın işlenemez.",
        "3) Personal data shall be processed lawfully.",
        "(12) Kurul toplantılarını Başkan yönetir.",
    ],
)
def test_paragraph_numbers_are_not_facts(sentence: str) -> None:
    """Renumbering "(2)" to "(1)" leaves the provision true. Labelling it a
    hallucination put true claims in the loss — found in the first kvkk-tr run,
    where it made the verifier look worse than it is."""
    marker = sentence.split(" ", 1)[0]
    for seed in range(40):
        result = corrupt(sentence, "tr" if "Kişisel" in sentence or "Kurul" in sentence else "en",
                         _rng(seed), kinds=("number",))
        if result is not None:
            assert result.corrupted.startswith(marker), result.corrupted


def test_a_verb_ending_in_tur_is_not_a_copula() -> None:
    """"yürütür" is yürüt- + aorist, not a noun + "-tür": after a vowel the
    copula is always d-initial. Treating it as one produced "yürü değildir"."""
    result = corrupt("Bu Yönetmelik hükümlerini Başkan yürütür.", "tr", _rng(), kinds=("negation",))
    assert result is None or "yürü değildir" not in result.corrupted


@pytest.mark.parametrize(
    ("word", "expected"),
    [("sorumludur", "sorumlu değildir"), ("şarttır", "şart değildir"), ("gündür", "gün değildir")],
)
def test_real_copulas_still_negate(word: str, expected: str) -> None:
    result = corrupt(f"Bu {word}.", "tr", _rng(), kinds=("negation",))
    assert result is not None
    assert expected in result.corrupted


def test_amendment_annotations_and_bracketed_ids_are_not_facts() -> None:
    sentence = "(3) (Değişik:2/3/2024-7499/33 md.) Başvuru [sicil-md-07] incelenir."
    for seed in range(40):
        result = corrupt(sentence, "tr", _rng(seed), kinds=("number",))
        assert result is None, result


def test_day_of_month_stays_a_valid_day() -> None:
    for seed in range(60):
        result = corrupt("It was signed on June 28, 2018.", "en", _rng(seed), kinds=("number",))
        assert result is not None
        day = result.corrupted.split("June ")[1].split(",")[0]
        assert 1 <= int(day) <= 28


def test_number_words_inside_compound_adjectives_are_left_alone() -> None:
    assert corrupt("It measures the three-dimensional structure.", "en", _rng(), kinds=("number",)) is None
