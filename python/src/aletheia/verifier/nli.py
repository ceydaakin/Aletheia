"""Entailment scoring backends.

The verifier answers one question per claim: *do the chunks this claim cites
entail it?* Not "are they about the same topic" — the failure this whole project
exists to catch is a fluent sentence that resembles its evidence and contradicts
it. Lexical overlap cannot tell "otuz gün" from "altmış gün"; an NLI model can.

Two backends:

* :class:`OverlapScorer` — the week-2 placeholder, kept because it needs no
  model and is a legitimate ablation baseline ("what does entailment buy over
  string similarity?", PRD §7.3b). It is never the default in a configuration
  that quotes a bound.
* :class:`NLIScorer` — a multilingual NLI model scoring P(entailment). Behind the
  ``models`` extra.

Both take the *cited* chunks as premise. A claim with no citation is never sent
to a model: plausibility is not evidence, and a model asked to judge a claim
against an empty premise will happily return a number.
"""

from __future__ import annotations

import logging
import re
from typing import Protocol

from aletheia.settings import Settings

log = logging.getLogger("verifier.nli")

_WORD = re.compile(r"\w+", re.UNICODE)

_STOPWORDS = {
    "the", "a", "an", "of", "to", "in", "for", "and", "or", "by", "is", "are",
    "this", "that", "be", "as", "at", "it", "on", "with",
    "ve", "veya", "ile", "bir", "bu", "şu", "için", "olarak", "de", "da", "ki",
}


class Scorer(Protocol):
    name: str

    def score(self, pairs: list[tuple[str, str]]) -> list[float]:
        """Score (premise, hypothesis) pairs. Returns support in [0, 1]."""
        ...


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _WORD.findall(text)} - _STOPWORDS


class OverlapScorer:
    """Token overlap. A baseline, not an approximation of entailment.

    Cannot distinguish a claim from its negation, and cannot tell one number from
    another — which is precisely the failure mode the product is about. Reported
    in the ablation table so that the entailment model's contribution is a
    measured quantity rather than an assumption.
    """

    name = "overlap"

    def score(self, pairs: list[tuple[str, str]]) -> list[float]:
        scores = []
        for premise, hypothesis in pairs:
            hypothesis_tokens = _tokens(hypothesis)
            if not hypothesis_tokens or not premise.strip():
                scores.append(0.0)
                continue
            scores.append(len(hypothesis_tokens & _tokens(premise)) / len(hypothesis_tokens))
        return scores


class NLIScorer:
    """P(entailment | premise, hypothesis) from a multilingual NLI model.

    The model is loaded on first use so that importing this module never pulls a
    gigabyte into a process that may only be serving health checks.

    Long premises are truncated by the tokenizer rather than by us. That is a
    real limitation: a claim entailed only by the tail of a long chunk can be
    scored as unsupported. It biases towards abstention rather than towards
    unsupported answers, which is the direction this system prefers to fail in.
    """

    name = "nli"

    def __init__(self, model_name: str, *, batch_size: int = 16, max_length: int = 512) -> None:
        self.model_name = model_name
        self.batch_size = batch_size
        self.max_length = max_length
        self._pipeline = None
        self._entail_index: int | None = None

    def _load(self):
        if self._pipeline is None:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            log.info("loading NLI model", extra={"extra_fields": {"model": self.model_name}})
            tokenizer = AutoTokenizer.from_pretrained(self.model_name)
            model = AutoModelForSequenceClassification.from_pretrained(self.model_name)
            model.eval()

            # Label order differs between NLI checkpoints; reading it from the
            # config beats assuming index 0 and silently scoring contradiction as
            # support.
            labels = {v.lower(): k for k, v in model.config.id2label.items()}
            if "entailment" not in labels:
                raise ValueError(
                    f"{self.model_name} does not expose an 'entailment' label "
                    f"(has {sorted(labels)}); it is not an NLI checkpoint"
                )
            self._entail_index = labels["entailment"]
            self._pipeline = (tokenizer, model, torch)
        return self._pipeline

    def score(self, pairs: list[tuple[str, str]]) -> list[float]:
        if not pairs:
            return []
        tokenizer, model, torch = self._load()
        assert self._entail_index is not None

        scores: list[float] = []
        with torch.no_grad():
            for start in range(0, len(pairs), self.batch_size):
                batch = pairs[start : start + self.batch_size]
                encoded = tokenizer(
                    [premise for premise, _ in batch],
                    [hypothesis for _, hypothesis in batch],
                    truncation=True,
                    padding=True,
                    max_length=self.max_length,
                    return_tensors="pt",
                )
                logits = model(**encoded).logits
                probabilities = torch.softmax(logits, dim=-1)
                scores.extend(probabilities[:, self._entail_index].tolist())
        return scores


def get_scorer(settings: Settings) -> Scorer:
    backend = settings.verifier_backend.lower()
    if backend in ("overlap", "null", ""):
        return OverlapScorer()
    if backend in ("nli", "entailment"):
        return NLIScorer(settings.nli_model, batch_size=settings.verifier_batch_size)
    raise ValueError(f"unknown VERIFIER_BACKEND {settings.verifier_backend!r}")
