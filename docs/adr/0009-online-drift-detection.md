# ADR-0009 — Test exchangeability on-line with a conformal test martingale

- **Status:** Accepted
- **Date:** 2026-09-26
- **Relates to:** ADR-0004, ADR-0007, PRD §5.2 and risk table, report §7.4

## Context

The guarantee holds only while live traffic is exchangeable with the
calibration set. Until now that was approximated by calibration age: older than
`CALIBRATION_MAX_AGE_HOURS`, stop quoting it. Age is a proxy — a corpus can
change an hour after calibration, and traffic can shift without the calendar
noticing. The PRD makes drift detection part of the product, not an add-on:
"when drift is detected the system enters degraded mode and says so".

The obvious detector — a two-sample test (KS, chi-square) on a rolling window,
run on every request — is wrong in a way that matters here. Each check has its
own false-alarm probability, and checking thousands of times drives the chance
of *some* false alarm towards one. A monitor that eventually fires on a healthy
system trains operators to ignore it.

## Decision

Two signals, both of which withdraw the guarantee (`abstain`, reason
`drift_detected`, `degraded: true`):

1. **Corpus change.** Any `corpus_events` row for the tenant after the
   calibration's `corpus_known_at`. No tolerance: "how much change is fine"
   would be a second threshold, uncalibrated, in front of the calibrated one.

2. **Statistic drift, tested on-line.** Every live risk statistic gets a
   conformal p-value — its randomised rank among the calibration statistics and
   every live statistic before it. Under exchangeability those p-values are
   independent and uniform, so a betting strategy against them is a test
   martingale, and by Ville's inequality

       P(the martingale ever reaches L) <= 1/L

   **over the calibration's entire lifetime**, however often it is checked. The
   strategy is Vovk's Simple Jumper, which redistributes a small share of wealth
   each step so that a change after a long stable stretch is still caught
   quickly. `L = DRIFT_ALARM_LEVEL = 100`: at most a 1% chance of ever alarming
   on exchangeable traffic.

The alarm is latched per calibration; only a new calibration clears it. State
is a row per calibration updated under a row lock, and the observations are
kept, so the martingale is reproducible from the table.

## Consequences

- **It tests the statistic, not the loss.** Live traffic has no labels, so a
  shift in P(loss | statistic) that leaves the statistic's distribution alone is
  invisible. This is the part of exchangeability that can be tested without
  labels; the report says which part it is.
- **It fires in both directions.** Traffic that looks *safer* than calibration
  also alarms. That is deliberate: the bound says nothing about traffic it was
  not fitted on, whichever way it moved.
- **Detection is not instant.** A single outlier cannot alarm (tested); a
  sustained shift is caught within tens of requests in simulation. Until then
  the old bound is quoted. The alternative — alarming fast — is paid for in false
  alarms, which this design refuses to trade for.
- **One more write per request.** The controller now writes an observation and
  updates the monitor row in a transaction. Measured cost is small against the
  verifier; it serialises requests per calibration, which is the correct
  semantics for a sequential test.
- `CALIBRATION_MAX_AGE_HOURS` stays as a backstop, not the mechanism.
