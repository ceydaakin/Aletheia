"""Enforces the service boundary from ADR-0003.

Sub-packages of :mod:`aletheia` are services that happen to ship in one
distribution. They may share ``contracts``, ``settings``, ``service``, ``db``, and
``metrics`` — and nothing else. The moment the risk controller imports the
verifier's model code directly, the boundary is packaging fiction and splitting
the services later stops being mechanical.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SERVICES = ("retrieval", "generation", "verifier", "risk", "ingestion")
SRC = Path(__file__).resolve().parents[1] / "src" / "aletheia"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


@pytest.mark.parametrize("service", SERVICES)
def test_services_do_not_import_each_other(service: str) -> None:
    others = {s for s in SERVICES if s != service}
    offences: list[str] = []

    for path in (SRC / service).rglob("*.py"):
        for module in _imported_modules(path):
            parts = module.split(".")
            if len(parts) >= 2 and parts[0] == "aletheia" and parts[1] in others:
                offences.append(f"{path.relative_to(SRC)} imports {module}")

    assert not offences, (
        "services must communicate over HTTP using aletheia.contracts, "
        "never by importing each other (ADR-0003):\n  " + "\n  ".join(offences)
    )


def test_every_service_package_exists() -> None:
    """Guards against a rename silently disabling the check above."""
    for service in SERVICES:
        assert (SRC / service / "__init__.py").is_file(), f"missing package: {service}"
