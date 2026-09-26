# Review notes for the drafted evaluation sets

Every query in `kvkk-tr`, `kvkk-en` and `en-public` was drafted by Claude
(Anthropic) from the corpus and is `status: draft` until a person verifies it:

```bash
make review DATASET=kvkk-tr PARALLEL=kvkk-en      # one decision covers both languages
make review DATASET=en-public
```

What was checked mechanically, for every query, before it was merged
(`scripts/drafts.py check`, and again by `aletheia.eval.dataset.load`):

- every evidence quote appears **verbatim** (modulo whitespace and case) in one
  of the query's `relevant_docs`;
- every `relevant_docs` entry exists in the corpus;
- answerable queries have evidence and an answer; unanswerable ones have neither;
- ids are unique; parallel ids exist in both languages.

What a machine cannot check, and review is for: that the question is natural,
that the drafted answer is right and complete, that the evidence proves it, and —
for `unanswerable` — that the corpus really does not answer it. The drafters
grepped the corpus for every unanswerable query's key terms, but a person should
treat that as a claim to verify, not a fact.

Until review is done, every experiment reports its query count as drafts, and
`--verified-only` restricts any run to what a person has signed off.

## Items the drafters flagged for a closer look

### kvkk (parallel TR/EN)

- **kvkk-b-032, kvkk-b-033** rely on the 2024 amendment of art. 18(2) (whether a
  data processor can be fined) — worth a legal check.
- **kvkk-b (art. 30)** asks which Penal Code articles art. 30 amended: correct, but
  not a question a compliance officer would ask. Reject if it reads unnatural.
- **kvkk-b multi-doc pair art. 16(4) + sicil-md-13**: the Law says "derhâl", the
  Regulation says 7 days. The answer states both; check it reads as intended.
- **kvkk-a-085** is a deliberate trap inside the answerable set: it asks for the
  breach-notification deadline in hours; the right answer is that the Law gives
  none ("en kısa sürede"). Check it is phrased so that answer is fair.
- **kvkk-a-090** (provisional art. 1(3)): the English has a double negative; the
  item follows the Turkish meaning.
- **kvkk-a provisional art. 2**: the English translation diverges (annual vs
  unpaid leave, missing appointment window); the items avoid those points.
- **kvkk-a art. 9 items** list the matching `aktarim-*` file too, since the
  regulation restates most of art. 9.
- **kvkk-c multi-doc sicil-16(ğ) + silme-05** assumes a Board exemption from
  registration removes the policy duty — sound, but an inference.
- **kvkk-c**: silme-13/sicil-18 and silme-14/sicil-19 have identical text; each
  question names its regulation. Same answer either way.
- **kvkk-c sicil-14(2)**: TR and EN differ ("activity ends" vs "obligation
  relieved"); the answer covers both.
- **kvkk-d-076** says a standard contract needs no Board permission — the texts
  imply it rather than state it. **kvkk-d-079** applies the "incidental"
  definition to a one-off database access. Both need a legal eye.
- **kvkk-d**: EN art. 14(5) says "the Board", TR says "Kuruma"; EN art. 14(1)
  duplicates a phrase. The items avoid these.
- **kvkk-x-006 … x-008** (revalued fines for 2024–2026): the corpus only has the
  original art. 18 amounts. A system quoting those has fallen for the trap, but a
  reviewer could call it a partial answer. Decide once and apply consistently.
- **kvkk-x-014** (access-log retention): a system may cite the 3-year retention of
  destruction records (silme art. 7/3) — a trap by design; confirm it is not an answer.
- **kvkk-x-030** (VERBİS renewal interval): sicil art. 7/2(e) mentions an
  "expiration date" but no renewal period.
- **kvkk-x-003, x-005** (VERBİS thresholds and deadlines): the regulation names the
  criteria and leaves figures to Board decisions, which are not in the corpus.

### en-public

- **enp-br-048** (Millau peak toll): the source contradicts itself (€13.90 vs
  €11.20–13.70). The draft uses €13.90.
- **enp-br-042, HZMB multi-doc**: the source gives two values (204 m / 203.5 m;
  490 m / 491 m). Both should count as correct.
- **enp-br-005, enp-br-085** (Golden Gate deaths): "eleven men were killed in falls"
  is treated as the total.
- **enp-br-036** (Tower Bridge hydraulics): the file credits Rendel for design and
  Sugg & Co for installation; the draft answers Rendel.
- **Time-dependent answers**: the Golden Gate 2026–27 toll and the Çanakkale toll
  since 1 July 2026 are right for this snapshot only.
- **enp-sp-076**: the source says Surveyor 6 hopped "in 1976" (it was 1967); the
  question asks only for the lander.
- **enp-sp-089**: the Rosetta file dates arrival at 67P both May and 6 August
  2014; the draft uses 6 August.
- **enp-sp-082**: Voyager 1 also launched from LC-41, so a third file supports it.
- **enp-dp-105**: needs arithmetic; the answer is approximate ("about 28–29").
- **enp-dp CCPA vs LGPD "which first"** compares CCPA's effective date with the
  LGPD's backdated date — check the premise reads fairly.
- **enp-dp HIPAA penalties**: the file ends before the penalty table, so the annual
  cap is unanswerable *in this corpus* although true in the world.
- **enp-ax**: three evidence quotes carry LaTeX copied from the abstracts
  (`\textit{...}`, `KG$^2$RAG`, `5.0\%`). Near-miss pairs to double-check:
  1512-08849 vs 1810-08606 (86.1% vs 86.14% on SNLI); 2304-03870 vs 2310-11689
  (AUROC vs AUACC).
