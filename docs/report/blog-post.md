# In RAG, correctness is a parameter — not a hope

*Draft blog post. Companion to the [technical report](aletheia.md).*

> **Update, September 2026.** Written when the evaluation set had 27 questions and
> certified nothing. It now has 1,500 over a parallel Turkish–English legal corpus:
> every certified threshold held its α on held-out data, unsupported responses fell
> from 38–46% to 0.2–6.5%, and the headline finding is that a threshold certified in
> English misses its budget in Turkish in 84% of splits — because the same entailment
> model accepts 31% of swapped legal terms in Turkish and 1.3% in English. The report
> has the numbers; the argument below stands.

---

Every RAG demo ends the same way. A fluent paragraph, a list of sources
underneath, and a reader left to work out which sentence came from which source.
The system has no opinion about whether it just made something up.

For a lot of applications that is fine. For an insurance operations team reading
policy documents, one fabricated sentence is expensive, so the usual response is
to put a human in front of every output — which removes the reason to automate.

I spent twelve weeks building the thing I thought was missing: not a better
prompt, but a serving layer that treats the error rate as a **parameter you set**.

```json
{
  "query": "2024 sonrası imzalanan sözleşmelerde fesih ihbar süresi nedir?",
  "risk_budget": 0.05
}
```

If it cannot honour 0.05, it does not answer. It says so, and it tells you which
sources to read yourself.

## How it works, briefly

Every sentence of the answer is decomposed into an atomic claim carrying the
chunk ids it cites. Each claim is scored against **only** what it cites, with a
multilingual entailment model. The whole response gets one scalar risk score, and
that score is compared against a threshold chosen by a hypothesis test on a
held-out calibration set — Learn-then-Test, from the conformal prediction
literature.

Here is the number that makes the whole thing work. Given a premise saying *thirty
days*:

| claim | entailment | word overlap |
|---|---|---|
| "notice period is **thirty** days" | **0.99** | 0.75 |
| "notice period is **sixty** days" | **0.005** | 0.50 |

One word changes, and the entailment model moves from 0.99 to 0.005. Word overlap
barely notices — and at 0.50 it sits *at* the default acceptance threshold, so a
similarity-based checker ships the false claim as supported. That gap is the
entire product.

## Three things I got wrong

I want to write about the mistakes, because they were all invisible to reading and
obvious to measuring.

**A guarantee that was arithmetically valid and empirically false.** There are two
ways to define "error rate" for a system that can abstain. *Marginal:* how often
is a response wrong, counting abstentions as successes. *Selective:* how often is
a response wrong, given that we answered. I certified one and reported the other.
Both halves were correct. Nothing looked wrong. Held-out error exceeded the budget
in **56% of runs**.

The lesson is narrower than "test your code": a statistical guarantee needs a test
that checks *the guarantee*, not the arithmetic. Mine now generates data with a
known true error rate and asks whether certified thresholds actually hold.

**A recall floor of 2%.** Postgres's query parsers join search terms with AND, so
a passage had to contain every word of the question. Questions are longer than the
passages that answer them. Switching to disjunctive matching — which is what BM25
does anyway — took recall@1 from 0.02 to 0.58. Nobody catches that by reading
code; you catch it the moment you build an evaluation harness.

**An abstention that was really a timeout.** Under load, retrieval was starving
itself on database connections and hitting its deadline. The system did exactly
what it was designed to do: it abstained, politely, with sources attached. It
looked like a corpus gap. It was a connection pool.

That one still bothers me. **A system designed to fail gracefully can fail
invisibly.** Every abstention path that protects users also hides infrastructure
problems. The response now carries a `degraded` flag saying "this abstention was
our fault, not the corpus's" — because those are different problems and they look
identical from outside.

## What it does not do yet

The honest part.

The calibration currently certifies **nothing**, and that is the machinery
working. Certifying a threshold at α=0.05 over a 50-point grid requires at least
135 answered calibration responses — that falls straight out of the multiplicity
correction, independent of how good the system is. I have 27 labelled queries. So
it refuses, and reports the floor rather than shipping the least-bad threshold.

The default generator is *extractive*: it copies sentences from retrieved
passages. It literally cannot hallucinate. Any bound calibrated against it
measures retrieval quality and verifier strictness, not unsupported generation.
I've written that warning in four places because it is the easiest thing in the
world to forget when you're quoting a nice-looking number.

And the entailment model costs **13 seconds** to verify a four-claim answer on
CPU, against a 3.5-second latency budget. Quantization doesn't work on this
architecture. That is the largest open engineering problem, and the obvious cheap
fix — screening claims with word overlap before running the expensive model —
fails for the exact reason in the table above: overlap would wave the
contradictions through.

## Why bother

Because "≤5% unsupported claims, with 95% confidence, calibration run
`cal_2026_07_tr`" is a different kind of sentence from "our RAG system works
well." One can be checked. One can be wrong in a way you'd find out about.

The system is not finished. But the part that decides whether to speak or stay
quiet is real, it is tested, and when it does not know, it says so.

---

*Code: [github.com/ceydaakin/Aletheia](https://github.com/ceydaakin/Aletheia).
Architecture decisions in `docs/adr/`, including the two above that cost the most
to get right.*
