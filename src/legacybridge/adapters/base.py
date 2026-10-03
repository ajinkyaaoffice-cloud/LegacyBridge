"""Language-pair adapter interface. Everything source/target-language specific lives behind it.

The core pipeline (scheduler, oracle, translator, verifier, fuzzer, integrator) only talks to a
LanguageAdapter, so adding e.g. Python2->Python3 or JS->TS means writing one new adapter.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..sandbox import RunResult


@dataclass
class SourceModule:
    """One legacy module as discovered by the adapter."""

    name: str
    kind: str                        # e.g. program | subprogram | copybook
    path: Path
    source: str
    info: dict[str, Any]             # adapter-specific parse result (JSON-serializable)
    deps: list[tuple[str, str]] = field(default_factory=list)  # (dependency name, edge kind)
    executable: bool = True          # False for data-only modules (copybooks, headers)


@dataclass
class GeneratedInput:
    name: str
    text: str
    origin: str                      # property | random | mutation | llm | fuzz_llm ...


class LanguageAdapter(ABC):
    """Source->target language pair."""

    name: str = "base"
    source_language: str = "?"
    target_language: str = "?"

    def __init__(self, root: Path):
        self.root = Path(root)

    # ------------------------------------------------------------ discovery
    @abstractmethod
    def discover(self) -> list[SourceModule]:
        """Find and parse all modules under `root`."""

    @abstractmethod
    def load(self, modules: list[SourceModule]) -> None:
        """Re-hydrate adapter state from modules stored on the blackboard (for resume/verify)."""

    def features(self, module: str) -> list[str]:
        """Semantic feature tags for a module (used in prompts and lesson retrieval)."""
        return []

    def module_context(self, module: str) -> str:
        """Source text given to LLMs for this module (program + the data definitions it uses)."""
        raise NotImplementedError

    # ------------------------------------------------------------ legacy execution
    @abstractmethod
    async def run_legacy(self, module: str, stdin: str) -> RunResult:
        """Run the ORIGINAL module (in the sandbox) on one test input."""

    @abstractmethod
    def toolchain_status(self) -> tuple[bool, str]:
        """(available, message) for the legacy toolchain, e.g. GnuCOBOL."""

    # ------------------------------------------------------------ test inputs
    @abstractmethod
    def input_spec(self, module: str) -> str:
        """Human/LLM-readable description of the module's test-input format."""

    @abstractmethod
    def validate_input(self, module: str, text: str) -> str | None:
        """None if `text` is an acceptable input for `module`, else a reason."""

    def canonical_input(self, module: str, text: str) -> str:
        """Normalize an input (line endings, padding) before validation and storage."""
        return text

    def llm_case_instructions(self, module: str) -> str:
        """How an LLM must express a test case (JSON) for `module`."""
        return 'JSON shape: {"cases": [{"name": "...", "why": "...", "stdin": "<exact stdin text>"}]}'

    def parse_llm_case(self, module: str, case: dict[str, Any]) -> str:
        """Turn one LLM-proposed case into stdin text (raise ValueError if unusable)."""
        if not isinstance(case.get("stdin"), str):
            raise ValueError("'stdin' must be a string")
        return case["stdin"]

    def testable(self, module: str) -> str | None:
        """None if the module can be differentially tested, else why not."""
        return None

    @abstractmethod
    def property_inputs(self, module: str, n: int, rng: random.Random) -> list[GeneratedInput]:
        """Boundary-value inputs derived from data definitions."""

    @abstractmethod
    def random_inputs(self, module: str, n: int, rng: random.Random) -> list[GeneratedInput]:
        """Seeded random inputs derived from data definitions."""

    @abstractmethod
    def mutation_inputs(self, module: str, seeds: list[str], n: int, rng: random.Random) -> list[GeneratedInput]:
        """Adversarial mutations (literal boundaries, sign flips, overflow) of existing inputs."""

    # ------------------------------------------------------------ target side
    @abstractmethod
    def target_name(self, module: str) -> str:
        """Target-language module name (e.g. Python module name)."""

    @abstractmethod
    def interface_contract(self, module: str) -> str:
        """What the translation of `module` must expose (entry points, signatures, I/O)."""

    @abstractmethod
    def dependency_interface(self, module: str, code: str) -> str:
        """How dependents should call the verified translation of `module`."""

    @abstractmethod
    def idiom_guide(self) -> str:
        """Source->target semantics guide included in translator prompts."""

    @abstractmethod
    def compact_idiom_guide(self) -> str:
        """Shorter guide for small context windows."""

    @abstractmethod
    def check_target(self, module: str, code: str) -> str | None:
        """Static sanity check of a translation (syntax, required entry points). None if OK."""

    @abstractmethod
    async def run_target(self, module: str, code: dict[str, str], stdin: str) -> RunResult:
        """Run the TRANSLATED module (in the sandbox). `code`: target module name -> source,
        containing the unit under test plus every dependency."""

    @abstractmethod
    def package(self, code: dict[str, str], out_dir: Path, package_name: str) -> Path:
        """Write a runnable target package; returns its path."""

    @abstractmethod
    async def run_packaged(self, package_dir: Path, module: str, stdin: str) -> RunResult:
        """Run `module` from the assembled package (end-to-end checks)."""

    def scenarios(self) -> list[tuple[str, str, str]]:
        """Whole-program scenarios: (scenario name, module, stdin)."""
        return []

    def mock_brain(self):
        """Deterministic MockProvider brain for offline runs (optional)."""
        return None
