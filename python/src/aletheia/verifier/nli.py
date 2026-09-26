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

    def __init__(
        self,
        model_name: str,
        *,
        batch_size: int = 16,
        max_length: int = 512,
        quantize: bool = False,
        device: str = "cpu",
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.batch_size = batch_size
        # Premises are single cited chunks, which are ~1200 characters by the
        # chunking config — well under 512 tokens. Lowering this is the cheapest
        # latency lever available, because attention cost is quadratic in length.
        self.max_length = max_length
        self.quantize = quantize
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

            if self.quantize:
                model = self._try_quantize(model, tokenizer, torch)

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
            self.device = _resolve_device(self.device, torch)
            if self.device != "cpu":
                model = model.to(self.device)
            self._pipeline = (tokenizer, model, torch)
        return self._pipeline

    def _try_quantize(self, model, tokenizer, torch):
        """int8 dynamic quantization, validated before it is trusted.

        Roughly halves CPU inference time on architectures where it works. It does
        not work everywhere: DeBERTa-v2 on torch 2.13 + ONEDNN raises
        "data type of input should be float" — at *inference* time, not at
        quantization time, so quantizing and hoping would turn every verification
        request into a 500.

        A test forward pass settles it at startup. Falling back is safe in the one
        direction that matters: unquantized is the more accurate model, so the
        cost of this failing is latency, never a wrong support score.
        """
        try:
            quantized = torch.quantization.quantize_dynamic(
                model, {torch.nn.Linear}, dtype=torch.qint8
            )
            probe = tokenizer(["a"], ["b"], return_tensors="pt", truncation=True)
            with torch.no_grad():
                quantized(**probe)
        except Exception as exc:
            log.warning(
                "int8 quantization unavailable; continuing at full precision",
                extra={"extra_fields": {"model": self.model_name, "error": str(exc)}},
            )
            return model
        log.info("NLI model quantized to int8")
        return quantized

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
                ).to(self.device)
                logits = model(**encoded).logits
                probabilities = torch.softmax(logits, dim=-1)
                scores.extend(probabilities[:, self._entail_index].cpu().tolist())
        return scores


def _resolve_device(requested: str, torch) -> str:
    requested = requested.lower()
    if requested == "auto":
        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
        return "cpu"
    return requested


# Sentence boundaries for premise windows. The verifier may not import the
# generation package's splitter (ADR-0003), and needs less of it: a boundary is
# terminal punctuation followed by whitespace and something that starts a
# sentence or a list item, or a line break. "10.000" and "0.05" have no
# whitespace after the dot and never split.
_BOUNDARY = re.compile(r"(?<=[.!?…;:])\s+(?=[\w(\[«\"“])|\n+")


def premise_windows(premise: str, *, size: int = 2) -> list[str]:
    """Overlapping spans of ``size`` consecutive sentences, stride one.

    NLI models are trained on sentence-length premises. Handed a whole legal
    article, the model under-scores claims copied from it verbatim — measured at
    a 37% false-reject rate on kvkk-tr with 1200-character premises. Scoring
    against short windows and keeping the best (as SummaC does) asks the
    question the model was trained on.
    """
    units = [" ".join(u.split()) for u in _BOUNDARY.split(premise) if u and u.strip()]
    if not units:
        return []
    if len(units) <= size:
        return [" ".join(units)]
    return [" ".join(units[i : i + size]) for i in range(len(units) - size + 1)]


class WindowedNLIScorer:
    """Max entailment over premise windows.

    A claim is supported when *some* span of its cited evidence entails it.
    Taking the max cannot let a claim borrow support from outside its
    citations — every window is drawn from the cited text — which is the
    distinction from the uncited ablation.
    """

    name = "nli-window"

    def __init__(self, base: Scorer, *, size: int = 2) -> None:
        self.base = base
        self.size = size

    def score(self, pairs: list[tuple[str, str]]) -> list[float]:
        expanded: list[tuple[str, str]] = []
        owners: list[int] = []
        for index, (premise, hypothesis) in enumerate(pairs):
            for window in premise_windows(premise, size=self.size):
                expanded.append((window, hypothesis))
                owners.append(index)
        best = [0.0] * len(pairs)
        for owner, score in zip(owners, self.base.score(expanded) if expanded else [], strict=True):
            best[owner] = max(best[owner], float(score))
        return best


def get_scorer(settings: Settings) -> Scorer:
    backend = settings.verifier_backend.lower()
    if backend in ("overlap", "null", ""):
        return OverlapScorer()
    if backend in ("nli", "entailment"):
        return NLIScorer(
            settings.nli_model,
            batch_size=settings.verifier_batch_size,
            max_length=settings.verifier_max_length,
            quantize=settings.verifier_quantize,
            device=settings.verifier_device,
        )
    if backend in ("nli-window", "nli_window"):
        base = get_scorer(settings.model_copy(update={"verifier_backend": "nli"}))
        return WindowedNLIScorer(base, size=settings.verifier_window_sentences)
    raise ValueError(f"unknown VERIFIER_BACKEND {settings.verifier_backend!r}")
