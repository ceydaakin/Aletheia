# Corpora

Document files live here and are gitignored — the corpora are large, and two of
them (SEC filings, Resmî Gazete) are better fetched than vendored.

The directory is mounted read-only into the ingestion container at `/corpora`, so
a `source_uri` of `file:///corpora/tr-mevzuat/kvkk.pdf` resolves for the worker and
not only for whoever submitted it.

Load a tree:

```bash
python -m aletheia.ingestion.cli load ./corpora/tr-mevzuat --tenant demo --lang tr
python -m aletheia.ingestion.cli list --tenant demo
```

Document ids are derived from the path relative to the root you pass, so re-running
the same command amends the existing documents instead of creating a parallel
corpus. Supported formats: `.txt`, `.md`, `.html`, `.pdf`, `.docx`.

Planned layout (PRD §7.1):

```
corpora/
  en-public/       Wikipedia subset + arXiv abstracts
  en-domain/       SEC 10-K filings
  tr-domain/       Resmî Gazete / KVKK / legislation
  tr-adversarial/  150 trap questions — answers deliberately absent
```

Fetch scripts land with the eval harness in week 4.
