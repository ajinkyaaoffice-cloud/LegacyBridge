"""COBOL -> Python 3 adapter: discovery, legacy execution, test inputs, target-side checks."""

from __future__ import annotations

import random
import shutil
from pathlib import Path

from ...sandbox import RunResult
from ..base import GeneratedInput, LanguageAdapter, SourceModule
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

    # ------------------------------------------------------------ filled in by later phases
    async def run_legacy(self, module: str, stdin: str) -> RunResult:
        raise NotImplementedError

    def input_spec(self, module: str) -> str:
        raise NotImplementedError

    def validate_input(self, module: str, text: str) -> str | None:
        raise NotImplementedError

    def property_inputs(self, module: str, n: int, rng: random.Random) -> list[GeneratedInput]:
        raise NotImplementedError

    def random_inputs(self, module: str, n: int, rng: random.Random) -> list[GeneratedInput]:
        raise NotImplementedError

    def mutation_inputs(self, module: str, seeds: list[str], n: int, rng: random.Random) -> list[GeneratedInput]:
        raise NotImplementedError

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
