"""Aletheia — risk-controlled RAG gateway, model services.

Sub-packages are services, not libraries: they talk to each other over HTTP using
the types in :mod:`aletheia.contracts` and must never import one another
(ADR-0003). ``tests/test_architecture.py`` enforces that.
"""

__version__ = "0.1.0"
