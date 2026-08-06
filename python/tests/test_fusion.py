"""Reciprocal Rank Fusion (ADR-0006)."""

from __future__ import annotations

import pytest

from aletheia.retrieval.search import (
    RRF_K,
    Candidate,
    reciprocal_rank_fusion,
    ts_config,
    unique_langs,
)


def candidates(*chunk_ids: str, score: float = 0.0) -> list[Candidate]:
    return [
        Candidate(chunk_id=c, doc_id=c.split(":")[0], version=1, title="", text=c, lang="",
                  score=score)
        for c in chunk_ids
    ]


def ids(fused) -> list[str]:
    return [f.candidate.chunk_id for f in fused]


def test_single_arm_preserves_its_ranking() -> None:
    """What makes lexical-only operation work unchanged when no embeddings exist."""
    fused = reciprocal_rank_fusion({"lexical": candidates("a", "b", "c")})
    assert ids(fused) == ["a", "b", "c"]


def test_agreement_between_arms_beats_a_single_first_place() -> None:
    """A chunk both arms rank second outranks one that only one arm ranks first.

    This is the entire point of fusion: corroboration across independent failure
    modes is worth more than one arm's confidence.
    """
    fused = reciprocal_rank_fusion(
        {
            "lexical": candidates("only-lexical", "agreed"),
            "dense": candidates("only-dense", "agreed"),
        }
    )
    assert ids(fused)[0] == "agreed"


def test_score_is_the_sum_of_reciprocal_ranks() -> None:
    fused = reciprocal_rank_fusion(
        {"lexical": candidates("a"), "dense": candidates("x", "a")}, k=60
    )
    by_id = {f.candidate.chunk_id: f for f in fused}

    # 'a' is rank 1 lexically and rank 2 densely.
    assert by_id["a"].score == pytest.approx(1 / 61 + 1 / 62)
    assert by_id["x"].score == pytest.approx(1 / 61)


def test_ranks_are_reported_per_arm() -> None:
    """The ablation table and any surprising result need to be explainable."""
    fused = reciprocal_rank_fusion(
        {"lexical": candidates("x", "a"), "dense": candidates("a")}
    )
    by_id = {f.candidate.chunk_id: f for f in fused}

    assert by_id["a"].ranks == {"lexical": 2, "dense": 1}
    assert by_id["x"].ranks == {"lexical": 1}


def test_magnitude_is_ignored() -> None:
    """RRF uses rank only — the documented trade in ADR-0006.

    An arm that is overwhelmingly confident contributes exactly what an arm that
    is barely confident contributes. This test exists so that a future change to
    weighted fusion is a deliberate decision rather than an accident.
    """
    weak = reciprocal_rank_fusion({"lexical": candidates("a", "b", score=0.01)})
    strong = reciprocal_rank_fusion({"lexical": candidates("a", "b", score=99.0)})

    assert [f.score for f in weak] == [f.score for f in strong]


def test_deterministic_tie_breaking() -> None:
    """Eval reproducibility (PRD G6) depends on ties resolving the same way."""
    arms = {"lexical": candidates("b", "a"), "dense": candidates("a", "b")}
    first = ids(reciprocal_rank_fusion(arms))
    second = ids(reciprocal_rank_fusion(dict(reversed(list(arms.items())))))

    assert first == second == sorted(first)


def test_limit_truncates_after_fusing() -> None:
    fused = reciprocal_rank_fusion(
        {"lexical": candidates("a", "b", "c"), "dense": candidates("c")}, limit=2
    )
    # 'c' is corroborated, so it must survive truncation even though it was third
    # in the only arm that ranked it highly.
    assert "c" in ids(fused)
    assert len(fused) == 2


def test_empty_input() -> None:
    assert reciprocal_rank_fusion({}) == []
    assert reciprocal_rank_fusion({"lexical": []}) == []


def test_k_must_be_positive() -> None:
    with pytest.raises(ValueError):
        reciprocal_rank_fusion({"lexical": candidates("a")}, k=0)


def test_default_k_is_the_documented_constant() -> None:
    assert RRF_K == 60


def test_ts_config_mapping() -> None:
    assert ts_config("tr") == "turkish_unaccent"
    assert ts_config("tr-TR") == "turkish_unaccent"
    assert ts_config("EN") == "english_unaccent"
    assert ts_config("") == "simple"
    assert ts_config("de") == "simple"


def test_unique_langs_prefers_an_explicit_request() -> None:
    assert unique_langs("tr", ["en", "tr"]) == ["tr"]


def test_unique_langs_covers_every_configuration_in_the_corpus() -> None:
    """A bilingual tenant must not have one language stemmed with the wrong rules."""
    assert unique_langs("", ["en", "tr"]) == ["en", "tr"]


def test_unique_langs_collapses_languages_sharing_a_configuration() -> None:
    # 'de' and 'fr' both fall back to 'simple'; running that arm twice is waste.
    assert unique_langs("", ["de", "fr"]) == ["de"]


def test_unique_langs_falls_back_to_the_default_arm() -> None:
    assert unique_langs("", []) == [""]
