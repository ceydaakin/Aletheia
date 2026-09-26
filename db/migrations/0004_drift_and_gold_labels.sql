-- Gold labels and drift monitoring.
--
-- Two changes that belong together because both are about what the guarantee is
-- conditional on.
--
-- 1. Calibration labels no longer have to come from the verifier. A run can
--    read its loss from ground truth — injected hallucinations whose location is
--    known (python/src/aletheia/eval/hallucinate.py) — so the verifier's own
--    errors land inside the bound instead of beneath it (report §7.3).
--
-- 2. The exchangeability assumption is tested on-line instead of approximated by
--    calibration age (report §7.4): every live risk statistic is fed to a
--    conformal test martingale (python/src/aletheia/risk/drift.py), and a
--    calibration whose martingale crosses its alarm level stops being quoted.

BEGIN;

-- ---------------------------------------------------------------------------
-- Label provenance
-- ---------------------------------------------------------------------------

ALTER TABLE calibration_examples
    DROP CONSTRAINT calibration_examples_label_source_check;
ALTER TABLE calibration_examples
    ADD CONSTRAINT calibration_examples_label_source_check
    CHECK (label_source IN ('human', 'llm_judge', 'verifier', 'synthetic'));

-- Which loss the threshold was certified against, and at what injected
-- hallucination rate. A bound certified at rate 0.15 says nothing about a
-- generator that hallucinates twice as often, so the rate travels with it.
ALTER TABLE calibrations
    ADD COLUMN loss_source text NOT NULL DEFAULT 'verifier'
        CHECK (loss_source IN ('gold', 'verifier')),
    ADD COLUMN hallucination_rate double precision NOT NULL DEFAULT 0
        CHECK (hallucination_rate >= 0 AND hallucination_rate <= 1);

-- ---------------------------------------------------------------------------
-- Drift monitoring
-- ---------------------------------------------------------------------------

-- Live risk statistics, per calibration, in arrival order. The conformal
-- p-value of each one is its rank among the calibration statistics and every
-- live statistic before it, so the history is the monitor's input and is kept.
CREATE TABLE drift_observations (
    id              bigserial PRIMARY KEY,
    calibration_id  text NOT NULL REFERENCES calibrations(calibration_id) ON DELETE CASCADE,
    statistic       double precision NOT NULL CHECK (statistic >= 0 AND statistic <= 1),
    p_value         double precision NOT NULL CHECK (p_value >= 0 AND p_value <= 1),
    observed_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX drift_observations_run_idx ON drift_observations (calibration_id, statistic);

-- The martingale's state. One row per calibration, updated under a row lock so
-- that concurrent requests are applied one at a time — the martingale is a
-- sequence, and two requests reading the same state would each bet the same
-- wealth.
CREATE TABLE drift_monitors (
    calibration_id  text PRIMARY KEY REFERENCES calibrations(calibration_id) ON DELETE CASCADE,
    n               integer NOT NULL DEFAULT 0 CHECK (n >= 0),
    log_wealth      double precision NOT NULL DEFAULT 0,
    weights         double precision[] NOT NULL,
    -- Latched: set once and never cleared. A new calibration starts a new row.
    alarmed_at      timestamptz,
    -- Pinned at creation and never updated: Ville's inequality holds only for a
    -- level fixed before the sequence starts, so a later config change applies
    -- to new calibrations and never to a martingale already running.
    alarm_level     double precision NOT NULL CHECK (alarm_level > 1),
    jump            double precision NOT NULL CHECK (jump >= 0 AND jump <= 1),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

COMMIT;
