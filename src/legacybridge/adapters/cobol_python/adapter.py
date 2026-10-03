"""COBOL -> Python 3 adapter: discovery, legacy execution, test inputs, target-side checks."""

from __future__ import annotations

import random
import shutil
from pathlib import Path

from ...sandbox import RunResult
from ..base import GeneratedInput, LanguageAdapter, SourceModule
from .cobc import CobcRunner
from .driver import generate_driver
from .inputs import (InputGenerator, Literals, canonical, case_from_llm, harvest_literals, llm_instructions,
                     mutation_cases, property_cases, random_cases, validate)
from .iofmt import Layout, layout_for
from .parser import ModuleInfo, UnsupportedCobol, parse_copybook, parse_program

SOURCE_EXT = {".cbl", ".cob", ".cobol"}
COPY_EXT = {".cpy", ".copy"}


class CobolPythonAdapter(LanguageAdapter):
    """GnuCOBOL programs/subprograms + copybooks -> Python 3 modules using the `lbrt` runtime."""

    name = "cobol-python"
    source_language = "COBOL"
    target_language = "Python 3"

    def __init__(self, root: Path, *, sandbox=None, work_dir: Path | None = None, cobc: str | None = None):
        super().__init__(root)
        self.sandbox = sandbox
        self.work_dir = Path(work_dir) if work_dir else self.root / ".legacybridge" / "work"
        self.cobc = cobc or shutil.which("cobc")
        self.infos: dict[str, ModuleInfo] = {}
        self.sources: dict[str, str] = {}
        self._layouts: dict[str, Layout] = {}
        self._literals: dict[str, Literals] = {}
        self._cobc_runner: CobcRunner | None = None

    # ------------------------------------------------------------ discovery
    def copy_dirs(self) -> list[Path]:
        """Directories searched for copybooks: every directory under root holding a copybook."""
        dirs = sorted({p.parent for p in self.root.rglob("*") if p.suffix.lower() in COPY_EXT})
        return dirs or [self.root]

    def _files(self, exts: set[str]) -> list[Path]:
        skip = {".legacybridge", "reference_py", "scenarios", ".git"}
        return sorted(p for p in self.root.rglob("*")
                      if p.is_file() and p.suffix.lower() in exts and not skip & set(p.parts))

    def discover(self) -> list[SourceModule]:
        """Parse every program and copybook under root."""
        copy_dirs = self.copy_dirs()
        out: list[SourceModule] = []
        seen: dict[str, Path] = {}
        for path in self._files(COPY_EXT):
            info = parse_copybook(path)
            out.append(self._register(info, path, executable=False))
            seen[info.name] = path
        for path in self._files(SOURCE_EXT):
            info = parse_program(path, copy_dirs)
            if info.name in seen:
                raise UnsupportedCobol(f"duplicate module name {info.name}: {seen[info.name]} and {path}")
            seen[info.name] = path
            if info.dynamic_calls:
                raise UnsupportedCobol(f"{info.name}: dynamic CALL via identifier {info.dynamic_calls} "
                                       "is not supported (dependencies must be static)")
            out.append(self._register(info, path, executable=True))
        return out

    def _register(self, info: ModuleInfo, path: Path, executable: bool) -> SourceModule:
        source = path.read_text()
        self.infos[info.name] = info
        self.sources[info.name] = source
        deps = [(c, "call") for c in info.calls] + [(c, "copy") for c in info.copies]
        return SourceModule(name=info.name, kind=info.kind, path=path, source=source,
                            info=info.model_dump(), deps=deps, executable=executable)

    def load(self, modules: list[SourceModule]) -> None:
        """Re-hydrate from blackboard rows (resume / `verify` without re-parsing)."""
        for m in modules:
            self.infos[m.name] = ModuleInfo.model_validate(m.info)
            self.sources[m.name] = m.source

    def info(self, module: str) -> ModuleInfo:
        return self.infos[module]

    def features(self, module: str) -> list[str]:
        return list(self.infos[module].features)

    def module_context(self, module: str) -> str:
        """Program source followed by the copybooks it includes."""
        info = self.infos[module]
        parts = [f"*> ===== {module} ({info.kind}) =====\n{self.sources[module]}"]
        for c in info.copies:
            if c in self.sources:
                parts.append(f"*> ===== copybook {c} =====\n{self.sources[c]}")
        return "\n".join(parts)

    # ------------------------------------------------------------ toolchain
    def toolchain_status(self) -> tuple[bool, str]:
        if self.cobc:
            return True, f"GnuCOBOL found at {self.cobc}"
        return False, ("GnuCOBOL (cobc) not found. Install: `brew install gnucobol` (macOS) or "
                       "`sudo apt-get install gnucobol` (Debian/Ubuntu)")

    # ------------------------------------------------------------ legacy execution
    def layout(self, module: str) -> Layout:
        if module not in self._layouts:
            self._layouts[module] = layout_for(self.infos[module])
        return self._layouts[module]

    def testable(self, module: str) -> str | None:
        info = self.infos.get(module)
        if info is None or info.kind == "copybook":
            return f"{module} is not executable"
        try:
            self.layout(module)
        except UnsupportedCobol as exc:
            return str(exc)
        return None

    def callees(self, module: str) -> list[str]:
        """Transitive CALL closure of `module` (excluding itself)."""
        seen: set[str] = set()
        stack = list(self.infos[module].calls)
        while stack:
            m = stack.pop()
            if m in seen or m == module or m not in self.infos:
                continue
            seen.add(m)
            stack.extend(self.infos[m].calls)
        return sorted(seen)

    def _runner(self) -> CobcRunner:
        if self._cobc_runner is None:
            if not self.cobc:
                raise RuntimeError(self.toolchain_status()[1])
            if self.sandbox is None:
                raise RuntimeError("adapter has no sandbox")
            self._cobc_runner = CobcRunner(self.cobc, self.sandbox, self.work_dir, self.copy_dirs())
        return self._cobc_runner

    async def build_legacy(self, module: str) -> Path:
        """Compile (cached) the original module; subprograms get a generated driver."""
        info = self.infos[module]
        sources = [Path(self.infos[m].path) for m in [module, *self.callees(module)]]
        copybooks = [Path(i.path) for i in self.infos.values() if i.kind == "copybook"]
        driver = generate_driver(info, self.layout(module)) if info.kind == "subprogram" else None
        return await self._runner().build(sources, driver, copybooks)

    def driver_source(self, module: str) -> str | None:
        info = self.infos[module]
        return generate_driver(info, self.layout(module)) if info.kind == "subprogram" else None

    async def run_legacy(self, module: str, stdin: str) -> RunResult:
        exe = await self.build_legacy(module)
        return await self._runner().run(exe, stdin)

    # ------------------------------------------------------------ test inputs
    def _gen(self, module: str, rng: random.Random) -> InputGenerator:
        if module not in self._literals:
            self._literals[module] = harvest_literals(self.sources[module])
        return InputGenerator(self.layout(module), self._literals[module], rng)

    def input_spec(self, module: str) -> str:
        lay = self.layout(module)
        if lay.mode == "call":
            head = (f"{module} is a CALLed subprogram (USING {', '.join(lay.params)}). A test-harness driver "
                    "reads stdin: one line = one CALL; all parameter fields '|'-separated; numerics are "
                    "digits only with implied decimals, signed ones prefixed by '+'/'-'. After each CALL it "
                    "prints the same layout with the values left in the parameters.")
        else:
            head = (f"{module} is a main program; stdin is its input file (LINE SEQUENTIAL), one record per "
                    f"line, max {lay.max_length} chars. Output is whatever it DISPLAYs; exit code = RETURN-CODE.")
        recs = "\n".join(f"Record {r.name} ({r.length} chars):\n{r.describe()}" for r in lay.records)
        return f"{head}\n{recs}"

    def canonical_input(self, module: str, text: str) -> str:
        return canonical(self.layout(module), text)

    def validate_input(self, module: str, text: str) -> str | None:
        return validate(self.layout(module), text)

    def llm_case_instructions(self, module: str) -> str:
        return llm_instructions(self.layout(module))

    def parse_llm_case(self, module: str, case: dict) -> str:
        return case_from_llm(self.layout(module), case)

    def property_inputs(self, module: str, n: int, rng: random.Random) -> list[GeneratedInput]:
        return [GeneratedInput(f"property-{i}", t, "property")
                for i, t in enumerate(property_cases(self._gen(module, rng), n), 1)]

    def random_inputs(self, module: str, n: int, rng: random.Random) -> list[GeneratedInput]:
        return [GeneratedInput(f"random-{i}", t, "random")
                for i, t in enumerate(random_cases(self._gen(module, rng), n), 1)]

    def mutation_inputs(self, module: str, seeds: list[str], n: int, rng: random.Random) -> list[GeneratedInput]:
        return [GeneratedInput(f"mutation-{i}", t, "fuzz_mutation")
                for i, t in enumerate(mutation_cases(self._gen(module, rng), seeds, n), 1)]

    # ------------------------------------------------------------ target side (Phase 4)
    def target_name(self, module: str) -> str:
        raise NotImplementedError

    def interface_contract(self, module: str) -> str:
        raise NotImplementedError

    def dependency_interface(self, module: str, code: str) -> str:
        raise NotImplementedError

    def idiom_guide(self) -> str:
        raise NotImplementedError

    def compact_idiom_guide(self) -> str:
        raise NotImplementedError

    def check_target(self, module: str, code: str) -> str | None:
        raise NotImplementedError

    async def run_target(self, module: str, code: dict[str, str], stdin: str) -> RunResult:
        raise NotImplementedError

    def package(self, code: dict[str, str], out_dir: Path, package_name: str) -> Path:
        raise NotImplementedError

    async def run_packaged(self, package_dir: Path, module: str, stdin: str) -> RunResult:
        raise NotImplementedError

    def mock_brain(self):
        from .mock_brain import CobolMockBrain

        return CobolMockBrain(self)
