"""COBOL -> Python 3 adapter: discovery, legacy execution, test inputs, target-side checks."""

from __future__ import annotations

import ast
import hashlib
import json
import keyword
import random
import re
import shutil
import sys
import tempfile
from pathlib import Path

from ...sandbox import RunResult
from ..base import GeneratedInput, LanguageAdapter, SourceModule
from .cobc import CobcRunner
from .driver import generate_driver
from .inputs import (InputGenerator, Literals, canonical, case_from_llm, harvest_literals, llm_instructions,
                     mutation_cases, property_cases, random_cases, validate)
from .harness import generate_harness, param_keys
from .iofmt import Layout, layout_for
from .parser import ModuleInfo, UnsupportedCobol, parse_copybook, parse_program

SOURCE_EXT = {".cbl", ".cob", ".cobol"}
LBRT_PATH = Path(__file__).parent / "lbrt.py"
HARNESS_FILE = "_lb_harness.py"
RESERVED_TARGETS = {"lbrt", "main", "harness", "_lb_harness"}
SAFE_IMPORTS = {"decimal", "math", "sys", "re", "itertools", "functools", "collections", "dataclasses",
                "typing", "datetime", "string", "operator", "copy", "enum", "__future__"}
FORBIDDEN_NAMES = {"exec", "eval", "compile", "open", "__import__", "breakpoint"}
PACKAGE_MAIN = '''"""Run a migrated main program:  python -m <package> <PROGRAM> < input.txt"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
MAINS = {mains}


def run(argv):
    if len(argv) != 1 or argv[0].upper() not in MAINS:
        sys.stderr.write("usage: python -m <package> <PROGRAM> < input\\nprograms: " + ", ".join(MAINS) + "\\n")
        return 2
    module = __import__(MAINS[argv[0].upper()])
    rc = module.main()
    sys.stdout.flush()
    return (rc if isinstance(rc, int) and not isinstance(rc, bool) else 0) & 0xFF


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
'''
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

    # ------------------------------------------------------------ target side
    def target_name(self, module: str) -> str:
        """Python module (and, for subprograms, function) name for a COBOL program-id."""
        name = re.sub(r"[^a-z0-9_]", "_", module.lower())
        if name[0].isdigit():
            name = "p_" + name
        if name in sys.stdlib_module_names or keyword.iskeyword(name) or name in RESERVED_TARGETS:
            name += "_cbl"
        return name

    def _signature(self, module: str) -> str:
        info = self.infos[module]
        t = self.target_name(module)
        if info.kind != "subprogram":
            return f"def main() -> int"
        args = ", ".join(self._param_var(p) + ": dict" for p in self.layout(module).params)
        return f"def {t}({args}) -> int | None"

    @staticmethod
    def _param_var(param: str) -> str:
        return re.sub(r"[^a-z0-9_]", "_", param.lower())

    def _param_doc(self, module: str) -> str:
        lay = self.layout(module)
        keys = param_keys(lay)
        lines = []
        for param in lay.params:
            lines.append(f"  {self._param_var(param)}  (01 {param}):")
            for f, (pp, key) in zip(lay.records[0].fields, keys):
                if pp != param:
                    continue
                typ = (f"Decimal  PIC {f.f.pic}{' ' + f.f.usage if f.f.usage != 'DISPLAY' else ''}"
                       if f.numeric else f"str[{f.width}]  PIC {f.f.pic}")
                lines.append(f"    {key!r}: {typ}")
        return "\n".join(lines)

    def interface_contract(self, module: str) -> str:
        info = self.infos[module]
        t = self.target_name(module)
        deps = [d for d in info.calls if d in self.infos]
        dep_line = (f"It CALLs {', '.join(deps)}; import them as `import <name>` and call `<name>.<name>(...)` "
                    "(plain module imports keep CALL cycles working)." if deps else "")
        if info.kind == "subprogram":
            return (f"Write Python module `{t}` (file {t}.py) exposing exactly:\n    {self._signature(module)}\n"
                    "Each argument is a dict for one USING parameter, passed BY REFERENCE: read and update "
                    "its entries in place (never replace the dict). Keys are the COBOL elementary field "
                    "names; numeric values are decimal.Decimal, alphanumeric values are str of exactly the "
                    "field length.\n" + self._param_doc(module) + "\n"
                    "Every value you store into a numeric parameter field must be what COBOL would store "
                    "(use lbrt.store / lbrt.size_error): the harness rejects values that don't fit the PIC.\n"
                    "WORKING-STORAGE must be module-level state that persists between calls (exactly like "
                    "COBOL); it is initialized once at import. Return None (or the RETURN-CODE int if the "
                    "program sets RETURN-CODE).\n" + dep_line)
        lay = self.layout(module)
        recs = "\n".join(f"  record {r.name} ({r.length} chars):\n" + r.describe() for r in lay.records)
        return (f"Write Python module `{t}` (file {t}.py) exposing:\n    def main() -> int\n"
                "It must read its input file from sys.stdin (LINE SEQUENTIAL: use "
                f"lbrt.read_records(sys.stdin, {lay.max_length or 1})), write exactly what the COBOL program "
                "DISPLAYs to sys.stdout, and return RETURN-CODE (0 if never set). Do not run anything at "
                "import time (no top-level calls).\n" + (f"Input records:\n{recs}\n" if lay.records else "")
                + dep_line)

    def dependency_interface(self, module: str, code: str) -> str:
        info = self.infos[module]
        t = self.target_name(module)
        if info.kind != "subprogram":
            return f"{module}: main program `{t}.main()` (not callable from other programs)."
        return (f"CALL '{module}' -> `import {t}` then `{t}.{t}(...)` with signature\n    {self._signature(module)}\n"
                + self._param_doc(module))

    def idiom_guide(self) -> str:
        return (Path(__file__).parent / "idiom_guide.md").read_text()

    def compact_idiom_guide(self) -> str:
        return (Path(__file__).parent / "idiom_guide_compact.md").read_text()

    def allowed_imports(self) -> set[str]:
        return SAFE_IMPORTS | {"lbrt"} | {self.target_name(m) for m, i in self.infos.items() if i.kind != "copybook"}

    def check_target(self, module: str, code: str) -> str | None:
        """Static checks (parse only, never executed here): syntax, entry point, imports, builtins."""
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return f"SyntaxError: {exc.msg} (line {exc.lineno})"
        info = self.infos[module]
        entry = self.target_name(module) if info.kind == "subprogram" else "main"
        if not any(isinstance(n, ast.FunctionDef) and n.name == entry for n in tree.body):
            return f"missing top-level function `{entry}`"
        allowed = self.allowed_imports()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                bad = [a.name for a in node.names if a.name.split(".")[0] not in allowed]
            elif isinstance(node, ast.ImportFrom):
                bad = [node.module or "."] if (node.level or (node.module or "").split(".")[0] not in allowed) else []
            elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
                bad = [node.id]
            else:
                continue
            if bad:
                return f"not allowed: {', '.join(bad)} (allowed imports: {', '.join(sorted(allowed))})"
        return None

    def _code_dir(self, module: str, code: dict[str, str]) -> Path:
        """Write lbrt + translations + harness into a content-addressed directory (read-only for runs)."""
        harness = generate_harness(module, self.infos[module].kind, self.layout(module), self.target_name(module))
        h = hashlib.sha256(harness.encode() + b"\0" + LBRT_PATH.read_bytes())
        for name in sorted(code):
            h.update(name.encode() + b"\0" + code[name].encode() + b"\0")
        d = self.work_dir / "py" / h.hexdigest()[:20]
        if not (d / HARNESS_FILE).exists():
            tmp = Path(tempfile.mkdtemp(prefix="py-", dir=self._ensure(self.work_dir / "py")))
            shutil.copy(LBRT_PATH, tmp / "lbrt.py")
            for name, src in code.items():
                (tmp / f"{name}.py").write_text(src)
            (tmp / HARNESS_FILE).write_text(harness)
            try:
                tmp.rename(d)
            except OSError:          # another task created it concurrently
                shutil.rmtree(tmp, ignore_errors=True)
        return d

    @staticmethod
    def _ensure(p: Path) -> Path:
        p.mkdir(parents=True, exist_ok=True)
        return p

    async def run_target(self, module: str, code: dict[str, str], stdin: str) -> RunResult:
        if self.sandbox is None:
            raise RuntimeError("adapter has no sandbox")
        d = self._code_dir(module, code)
        run_dir = Path(tempfile.mkdtemp(prefix="pyrun-", dir=self._ensure(self.work_dir / "runs")))
        try:
            return await self.sandbox.run([sys.executable, "-I", "-S", "-B", str(d / HARNESS_FILE)],
                                          stdin=stdin, cwd=run_dir)
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)

    def package(self, code: dict[str, str], out_dir: Path, package_name: str) -> Path:
        """Flat package: lbrt.py + one module per program + __main__.py dispatcher + harnesses."""
        pkg = Path(out_dir) / package_name
        if pkg.exists():
            shutil.rmtree(pkg)
        (pkg / "harness").mkdir(parents=True)
        shutil.copy(LBRT_PATH, pkg / "lbrt.py")
        mains = {}
        for module, info in sorted(self.infos.items()):
            t = self.target_name(module)
            if info.kind == "copybook" or t not in code:
                continue
            (pkg / f"{t}.py").write_text(code[t])
            if info.kind == "program":
                mains[module] = t
            else:
                h = generate_harness(module, info.kind, self.layout(module), t)
                h = h.replace("os.path.dirname(os.path.abspath(__file__))",
                              "os.path.dirname(os.path.dirname(os.path.abspath(__file__)))")
                (pkg / "harness" / f"{t}.py").write_text(h)
        (pkg / "__init__.py").write_text(f'"""{package_name}: Python translation generated by LegacyBridge."""\n')
        (pkg / "__main__.py").write_text(PACKAGE_MAIN.format(mains=json.dumps(mains, indent=4)))
        return pkg

    async def run_packaged(self, package_dir: Path, module: str, stdin: str) -> RunResult:
        if self.sandbox is None:
            raise RuntimeError("adapter has no sandbox")
        run_dir = Path(tempfile.mkdtemp(prefix="pkgrun-", dir=self._ensure(self.work_dir / "runs")))
        try:
            return await self.sandbox.run([sys.executable, "-I", "-S", "-B", str(Path(package_dir) / "__main__.py"),
                                           module], stdin=stdin, cwd=run_dir)
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)

    def describe_mismatch(self, module: str, stdin: str, expected: str, actual: str) -> str:
        """For subprograms: per CALL, which parameter fields differ (expected vs actual)."""
        lay = self.layout(module)
        if lay.mode != "call":
            return ""
        rec = lay.records[0]
        ins, exp, got = stdin.rstrip("\n").split("\n"), expected.split("\n"), actual.split("\n")
        out = []
        for i, (e, a) in enumerate(zip(exp, got), 1):
            if e == a or not a or len(a) != len(e):
                continue
            fields = [f"{f.key}: expected {x!r} got {y!r}" for f, x, y in zip(rec.fields, rec.split(e), rec.split(a))
                      if x != y]
            out.append(f"CALL #{i}" + (f" (input {ins[i - 1]!r})" if i <= len(ins) else "") + ": " + "; ".join(fields))
            if len(out) >= 3:
                break
        return "\n".join(out)

    def mock_brain(self):
        from .mock_brain import CobolMockBrain

        return CobolMockBrain(self)
