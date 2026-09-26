"""Windowed entailment: score a claim against short spans of its evidence."""

from __future__ import annotations

from aletheia.verifier.nli import WindowedNLIScorer, premise_windows


class FakeBase:
    """Entails only when the premise is short and contains the hypothesis —
    the behaviour of an NLI model trained on sentence-length premises."""

    name = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def score(self, pairs):
        self.calls.extend(pairs)
        return [1.0 if h in p and len(p) < 120 else 0.1 for p, h in pairs]


LONG = (
    "(1) Veri sorumlusu başvuruyu en geç otuz gün içinde sonuçlandırır. "
    "(2) İşlem ayrıca bir maliyet gerektiriyorsa Kurulca belirlenen tarifedeki ücret alınabilir. "
    "(3) Veri sorumlusu talebi kabul eder veya gerekçesini açıklayarak reddeder.\n"
    "a) Birinci bent.\nb) İkinci bent."
)


def test_windows_cover_every_sentence_and_list_item() -> None:
    windows = premise_windows(LONG, size=1)
    assert any("otuz gün" in w for w in windows)
    assert any(w.startswith("a) Birinci") for w in windows)
    assert any(w.startswith("b) İkinci") for w in windows)


def test_windows_of_two_overlap() -> None:
    windows = premise_windows("A bir. B iki. C üç.", size=2)
    assert windows == ["A bir. B iki.", "B iki. C üç."]


def test_short_premise_is_its_own_window() -> None:
    assert premise_windows("Tek cümle.", size=2) == ["Tek cümle."]


def test_decimals_do_not_split() -> None:
    windows = premise_windows("Ücret 10.000 TL olarak belirlenir. Sonra gelir.", size=1)
    assert windows[0] == "Ücret 10.000 TL olarak belirlenir."


def test_score_is_the_max_over_windows() -> None:
    base = FakeBase()
    scorer = WindowedNLIScorer(base, size=1)
    hypothesis = "Veri sorumlusu başvuruyu en geç otuz gün içinde sonuçlandırır."
    (score,) = scorer.score([(LONG, hypothesis)])
    assert score == 1.0
    assert base.score([(LONG, hypothesis)]) == [0.1], "the long premise alone fails"


def test_scores_stay_aligned_across_pairs() -> None:
    scorer = WindowedNLIScorer(FakeBase(), size=1)
    scores = scorer.score([("X yok. Y var.", "Y var."), ("Z var.", "Q yok."), ("", "boş")])
    assert scores == [1.0, 0.1, 0.0]
