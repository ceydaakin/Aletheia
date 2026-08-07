"""Claim decomposition.

The cases that matter are the ones that shred Turkish legal prose: thousands
separators written with a period, ordinals written as "1.", and abbreviations.
Each of those splits a claim in half if handled naively, and half a claim is
unverifiable — it becomes an abstention.
"""

from __future__ import annotations

import pytest

from aletheia.generation.decompose import (
    decompose,
    split_clauses,
    split_sentences,
    strip_citations,
)

# --- Sentence splitting ----------------------------------------------------


def test_splits_on_terminal_punctuation() -> None:
    assert split_sentences("Bir. İki! Üç?") == ["Bir.", "İki!", "Üç?"]


def test_turkish_thousands_separator_is_not_a_sentence_end() -> None:
    """'2.500 TL' is one number. Splitting it produces two unverifiable halves."""
    text = "Konaklama limiti 2.500 TL olarak uygulanır."
    assert split_sentences(text) == [text]


def test_decimal_is_not_a_sentence_end() -> None:
    assert split_sentences("The budget is 0.05 per query.") == [
        "The budget is 0.05 per query."
    ]


def test_turkish_ordinal_is_not_a_sentence_end() -> None:
    """Turkish writes ordinals as '1.', and the next word is lowercase."""
    text = "Bu konu 5. maddede düzenlenmiştir."
    assert split_sentences(text) == [text]


def test_abbreviations_do_not_split() -> None:
    assert split_sentences("Bkz. madde 7 ve vb. hükümler.") == [
        "Bkz. madde 7 ve vb. hükümler."
    ]
    assert split_sentences("See e.g. section 4 for details.") == [
        "See e.g. section 4 for details."
    ]


def test_periods_are_restored_after_masking() -> None:
    """The masking must be invisible in the output, or claims carry a private-use
    codepoint into the verifier and the response."""
    text = "Limit 2.500 TL. İkinci cümle."
    parts = split_sentences(text)

    assert parts == ["Limit 2.500 TL.", "İkinci cümle."]
    assert all("" not in p for p in parts)


def test_multiple_sentences_with_numbers_and_abbreviations() -> None:
    text = (
        "Fesih ihbar süresi 30 gündür. Bkz. madde 7. "
        "Tedarikçi faturaları 2.500 TL üzerindeyse onay gerekir."
    )
    assert split_sentences(text) == [
        "Fesih ihbar süresi 30 gündür.",
        "Bkz. madde 7.",
        "Tedarikçi faturaları 2.500 TL üzerindeyse onay gerekir.",
    ]


@pytest.mark.parametrize("text", ["", "   ", "\n\n"])
def test_empty_input(text: str) -> None:
    assert split_sentences(text) == []


def test_sentence_without_terminal_punctuation_survives() -> None:
    assert split_sentences("Bir cümle noktasız") == ["Bir cümle noktasız"]


# --- Citations -------------------------------------------------------------


def test_strips_a_single_citation() -> None:
    prose, citations = strip_citations("Süre otuz gündür. [doc:v1:chunk_0001]")
    assert prose == "Süre otuz gündür."
    assert citations == ("doc:v1:chunk_0001",)


def test_strips_multiple_citations() -> None:
    prose, citations = strip_citations("Bir iddia. [a:v1:chunk_0001][b:v2:chunk_0003]")
    assert prose == "Bir iddia."
    assert citations == ("a:v1:chunk_0001", "b:v2:chunk_0003")


def test_no_citation_is_not_an_error() -> None:
    """An uncited claim is the case the verifier exists to catch, not a parse failure."""
    prose, citations = strip_citations("Desteksiz bir iddia.")
    assert prose == "Desteksiz bir iddia."
    assert citations == ()


def test_brackets_inside_prose_are_left_alone() -> None:
    prose, citations = strip_citations("Madde [7] uygulanır.")
    assert prose == "Madde [7] uygulanır."
    assert citations == ()


# --- Clause splitting ------------------------------------------------------


def test_splits_two_substantial_clauses() -> None:
    sentence = (
        "Fesih ihbar süresi otuz gündür, ancak haklı sebeple fesihte ihbar aranmaz."
    )
    parts = split_clauses(sentence)

    assert len(parts) == 2
    assert parts[0].endswith(".")
    assert "haklı sebeple" in parts[1]


def test_does_not_split_a_noun_phrase_conjunction() -> None:
    """Without a comma, 've' usually joins noun phrases, not clauses."""
    sentence = "Fesih ve ihbar koşulları bu maddede düzenlenmiştir."
    assert split_clauses(sentence) == [sentence]


def test_does_not_split_when_a_side_would_be_a_fragment() -> None:
    """An unverifiable fragment costs answer rate on every query it appears in."""
    sentence = "Bu süre otuz gündür, ve ayrıca uygulanır."
    assert split_clauses(sentence) == [sentence]


def test_english_clause_split() -> None:
    sentence = (
        "The notice period is thirty days, but termination for cause requires none."
    )
    parts = split_clauses(sentence)
    assert len(parts) == 2


# --- End to end ------------------------------------------------------------


def test_decompose_carries_citations_to_every_claim() -> None:
    answer = (
        "Fesih ihbar süresi otuz gündür, ancak haklı sebeple fesihte ihbar aranmaz. "
        "[doc:v1:chunk_0001] "
        "Bu kural kamu sözleşmelerinde de geçerlidir."
    )
    claims = decompose(answer)

    assert len(claims) == 3
    # Both halves of the split sentence inherit its citation.
    assert claims[0].citations == ["doc:v1:chunk_0001"]
    assert claims[1].citations == ["doc:v1:chunk_0001"]
    # The uncited sentence stays uncited — this is what makes it unsupported.
    assert claims[2].citations == []


def test_decompose_of_an_empty_answer() -> None:
    assert decompose("") == []
    assert decompose("   \n  ") == []


def test_decompose_is_deterministic() -> None:
    """Eval reproducibility (PRD G6)."""
    answer = "Bir. İki. [a:v1:chunk_0001] Üç."
    assert decompose(answer) == decompose(answer)


def test_decompose_preserves_all_prose() -> None:
    """No text may vanish: a dropped claim is an assertion that reaches the user
    without ever being verified."""
    answer = (
        "Fesih ihbar süresi otuz gündür. [a:v1:chunk_0001] "
        "Limit 2.500 TL olarak uygulanır. [b:v1:chunk_0002]"
    )
    claims = decompose(answer)
    joined = " ".join(c.text for c in claims)

    for fragment in ["otuz gündür", "2.500 TL"]:
        assert fragment in joined
