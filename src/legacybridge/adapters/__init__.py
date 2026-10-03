"""Language adapters. `get_adapter` picks one by name."""

from __future__ import annotations

from pathlib import Path

from .base import LanguageAdapter


def get_adapter(name: str, root: Path, **kwargs) -> LanguageAdapter:
    """Instantiate an adapter by name ('cobol-python' is the only one implemented)."""
    if name in ("cobol-python", "cobol_python", "cobol"):
        from .cobol_python.adapter import CobolPythonAdapter

        return CobolPythonAdapter(root, **kwargs)
    raise ValueError(f"unknown adapter {name!r}; available: cobol-python")
