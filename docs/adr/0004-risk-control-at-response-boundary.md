# ADR-0004 — Risk control applies to the whole response, not to individual claims

- **Status:** Accepted
- **Date:** 2026-08-04
- **Relates to:** PRD §5.2, F6, F7, G1; open questions 1 and 4

## Context

Learn-then-Test and RCPS give a distribution-free guarantee of the form: choose a threshold
λ on a calibration set, and the risk on exchangeable future data is bounded by α with
confidence 1−δ. To apply the machinery, we must first decide **what unit the risk is
defined over**, because the guarantee is only as meaningful as that choice.

Two candidates, and they are not the same statement:

1. **Per-claim.** "Each individual claim we emit is unsupported with probability ≤ α."
2. **Per-response.** "Each answer we return contains at least one unsupported claim with
   probability ≤ α."

The distinction is not academic. A ten-claim answer under a per-claim 5% bound has a
roughly 40% chance of containing at least one unsupported claim, assuming independence
that does not hold anyway. A user who reads "≤5% error" and receives that answer has been
misled, and the misleading was done by the choice of unit, not by the statistics.

## Decision

**The risk is defined over the response.** The controlled quantity is:

> P(the returned response contains ≥ 1 unsupported claim) ≤ α, with confidence 1−δ.

Consequences of that choice, all deliberate:

- The **calibration label is response-level**: for each calibration example, "does this
  answer contain an unsupported claim?" — one binary label per response, matching PRD §5.2.
- The **confidence statistic is response-level** and monotone in answer quality. The
  leading candidate is `min_i support_score_i` over the claims, which directly tracks the
  weakest link the guarantee is about. Alternatives (mean support, unsupported fraction, a
  learned scorer) are evaluated empirically in week 6 — but whichever wins must be a single
  scalar per response, because that is what the threshold is applied to.
- **`answer_with_flags` is inside the guarantee, not an escape from it.** When claims are
  removed or flagged, the statistic is recomputed on the *post-edit* response. The bound
  applies to what the user actually receives. Removing a bad claim and then quoting a bound
  computed before the removal would be circular.
- The **guarantee string in the response names its calibration run** (`calibration_id`).
  A bound without the provenance of the calibration that produced it is a marketing claim.

## Consequences

**Good.** The guarantee matches the sentence a user would naturally read off the API
response, which is the only interpretation that matters for the regulated-sector persona.
It is also the conservative choice: a per-response bound implies a per-claim bound, not the
reverse.

**Bad, and accepted.** Per-response control is strictly harder to satisfy, so answer rate
(G2, ≥70% at α=0.05) is under real pressure. Long answers are penalised most, since more
claims means more chances to trip the bound. This may push the system toward terser
answers — which for the primary persona is arguably correct behaviour, but it must be
measured, not assumed. If G2 proves unreachable at α=0.05, the honest response is to report
the achievable answer rate and the risk–coverage curve, **not** to quietly redefine the
risk unit to make the number look better.

**Open.** The verifier's own error rate is not yet folded into the bound. Our labels come
from an NLI model plus LLM-as-judge with partial human verification, so the calibration
labels are noisy — and risk control with noisy labels is an active research area, not a
solved one. Until it is addressed, the guarantee is conditional on verifier accuracy and
the technical report must say so in those words. See PRD open question 4.

## Alternatives considered

**Per-claim control.** Higher answer rate, easier to satisfy, and defensible in a paper if
stated precisely. Rejected because it is systematically misread by the person the product
is for, and a guarantee that is technically true and practically misleading is worse than
no guarantee.

**Both, reported separately.** Genuinely attractive and may still happen in the report as
an additional column. Rejected as the *runtime* behaviour because the API must gate on one
number, and shipping two bounds invites the user to quote the flattering one.
