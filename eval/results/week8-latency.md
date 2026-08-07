# Week 8 — latency measurement

Measured with `gateway/cmd/loadtest` against the full `docker compose` stack on a
single Windows laptop (Docker Desktop, CPU only). Not a production node — the PRD
targets a Hetzner box — so treat the absolute numbers as a lower bound on quality
and an upper bound on speed.

```
go run ./cmd/loadtest -c 20 -n 400
```

## Result: the pipeline is fast, the verifier is not

| configuration | p50 | p95 | throughput | served |
|---|---|---|---|---|
| overlap verifier, 20 concurrent | **84 ms** | **182 ms** | 207 req/s | 400/400 |

Against PRD §4.2 (p50 ≤ 1.8 s, p95 ≤ 3.5 s, ≥ 20 concurrent): **passes with two
orders of magnitude of headroom** — but only with the *overlap* verifier, which
is a baseline and not the product.

Mean per stage over 420 requests:

| stage | mean |
|---|---|
| retrieval | 46 ms |
| risk | 27 ms |
| generation | 13 ms |
| verifier (overlap) | 11 ms |

## The finding that matters: NLI verification blows the budget

mDeBERTa-v3-base on this CPU, premise = one cited chunk:

| claims per response | median |
|---|---|
| 1 | 4.1 s |
| 4 | 13.3 s |
| 8 | 27.5 s |

A four-claim answer costs **~13 s in the verifier alone** — roughly 4× the entire
p95 budget, before retrieval or generation. PRD §9 listed this exact risk
("latency bütçesi verifier yüzünden patlar"); it is now measured rather than
anticipated.

Mitigations tried:

- **Shorter premises** (`max_length` 512 → 256): 5.4 s → 4.9 s for four claims,
  about 9%. Premises are single chunks and were already short, so there was
  little padding to remove.
- **int8 dynamic quantization**: *does not work here.* DeBERTa-v2 on torch 2.13
  with the ONEDNN backend raises `qlinear_dynamic: data type of input should be
  float` — at inference time, not at quantization time. The scorer now validates
  quantization with a probe forward pass at startup and falls back to full
  precision with a warning, because the cost of that failure must be latency and
  never a wrong support score.

Still open, in rough order of expected value:

1. **GPU inference.** The obvious answer; the PRD's deployment target has none.
2. **A smaller entailment model.** mDeBERTa-base is 280M parameters. Distilled
   multilingual NLI models exist; the trade against the `otuz`/`altmış`
   discrimination has to be measured, not assumed.
3. **The cascade from PRD §9** — full verification only for low-confidence
   claims. Note the obvious cheap gate does *not* work: lexical overlap scores a
   contradiction at or above the support threshold, so gating on it would let
   exactly the wrong claims skip verification.
4. **Verify asynchronously** and stream the decision after the provisional
   answer. The SSE contract already separates the two, so the plumbing exists —
   but it changes what the p95 SLO is measuring, which is a product decision.

## A methodological note

The first version of the load tester reported **PASS at 5 ms p95 while the
gateway rejected 97% of requests with 429**. It was measuring the rejection path
and calling it the SLO. The tool now excludes non-2xx responses from the latency
sample and exits non-zero when under 90% of offered load was served — the same
discipline the risk controller applies to its own bound: a measurement taken over
a fraction of the load does not describe the system at that load.

That run also surfaced a real defect. Retrieval held one connection for the
language lookup *while* fanning out to one connection per search arm, so at
concurrency 20 requests starved each other on a 10-connection pool and every
retrieval hit its 1.2 s stage deadline — surfacing as `abstain(out_of_corpus)`
rather than as any error. Releasing the lookup connection before the fan-out and
sizing the pool for fan-out rather than request count took p50 from 1274 ms to
84 ms and throughput from 15.7 to 207 req/s.
