# ADR-0007 — The controlled risk is selective, conditioned on having answered

- **Status:** Accepted
- **Date:** 2026-08-07
- **Relates to:** ADR-0004, PRD G1/G2, §5.2

## Context

ADR-0004 settled what the risk is defined *over*: a whole response, not an
individual claim. It did not settle what the denominator is, and that turns out to
be a second, independent choice with the same capacity to produce a bound that is
true on paper and false in practice.

A system that abstains has two ways to report its failure rate:

**Marginal.** P(the response is wrong *and* we answered). Every abstention counts
as a success, so the number falls simply by answering less.

**Selective.** P(the response is wrong | we answered). Abstentions are excluded
from the denominator entirely.

These are not close together. At a 30% answer rate they differ by a factor of
three, and the gap widens exactly as the system becomes more cautious — which is
to say, exactly in the regime this product is designed to operate in.

This was not a theoretical concern here. The first implementation certified the
marginal risk while the evaluation reported the selective one. Both were
individually correct; together they produced thresholds whose held-out risk
exceeded α in **56% of certified runs**. The bound was arithmetically valid and
empirically worthless, and nothing in the code looked wrong.

## Decision

**The controlled quantity is the selective risk.**

    R(λ) = P(response contains an unsupported claim | we answered at λ)

The Learn-then-Test p-value for a candidate λ is therefore computed against the
number of calibration responses that λ would have *answered*, not against the
calibration set size:

    p_λ = P(Bin(m_λ, α) ≤ k_λ)

where `m_λ` is the answered count and `k_λ` the failures among them.

Consequences that follow and are accepted:

- The test is **conditional on the selection event**. Given `m` answered
  responses the failure count is Binomial(m, R(λ)), so the exact binomial applies
  — but the guarantee is conditional on having answered, which is precisely the
  statement we want to make and not a weaker one smuggled in.
- `empirical_risk` on a calibration record is likewise selective. Storing the
  marginal alongside a selective bound would invite exactly the confusion this
  ADR exists to prevent.
- Coverage stays `answered / n`. It is a separate number and is reported
  separately: the risk–coverage curve is the pair, and quoting either alone hides
  the trade.

## Consequences

**Good.** The bound matches the sentence a user reads off the response. Someone
holding an answer wants to know the chance *that answer* is wrong; the fact that
the system declined ten other questions does not make the one in their hands any
safer. Selective is also the harder target, so it cannot be gamed by abstaining
more — under the marginal definition, a system that answers nothing has perfect
risk.

**Bad, and accepted.** Selective control needs more data. Because the denominator
shrinks with the answer rate, a conservative threshold that answers 10% of a
300-response calibration set has only 30 responses to certify against, and
Bonferroni over the λ grid will refuse. This is why calibration sets have to be
large, and it is a real cost rather than a formality.

**Bad.** There is a hard floor on calibration size. With zero observed failures
the p-value is (1−α)^m, so certification requires

    m ≥ ln(δ / |Λ|) / ln(1 − α)

— 135 answered responses at α=0.05, δ=0.05 over a 50-point grid, no matter how
good the system is. A 27-query dataset cannot produce a bound at any quality
level. `minimum_certifiable_n` computes this and the calibration job reports it
on failure, because "the verifier is bad" and "the sample cannot say anything"
look identical from the outside and have opposite fixes.

## Alternatives considered

**Control the marginal risk and report it as such.** Defensible, and easier to
certify. Rejected because the resulting sentence — "at most 5% of questions
produce a wrong answer" — is one users will read as the selective claim anyway,
and the difference favours the vendor. ADR-0004 already rejected the per-claim
unit on the same reasoning; this is the same mistake on a different axis.

**Report both.** Attractive for the technical report and likely to appear there.
Rejected as the runtime behaviour for ADR-0004's reason: the API must gate on one
number, and publishing two invites quoting the flattering one.
