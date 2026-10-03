"""Deterministic "brain" for MockProvider in offline runs (`--dry-run` and tests).

It dispatches on `req.meta["task"]` and answers from the parser output (summaries) and, for
translation tasks, from hand-written reference translations in `<project>/reference_py/`.
Real runs never use this module.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from ...llm.mock import default_brain
from ...llm.types import LLMRequest

if TYPE_CHECKING:
    from .adapter import CobolPythonAdapter


class CobolMockBrain:
    """Callable brain bound to an adapter (so it can see parsed module info)."""

    def __init__(self, adapter: "CobolPythonAdapter"):
        self.adapter = adapter

    def __call__(self, req: LLMRequest) -> str:
        task = req.meta.get("task")
        handler = getattr(self, f"do_{task}", None)
        return handler(req) if handler else default_brain(req)

    # ------------------------------------------------------------ tasks
    def do_summarize(self, req: LLMRequest) -> str:
        name = req.meta.get("module", "?")
        info = self.adapter.infos.get(name)
        if info is None:
            return f"- Module {name} (no parse information available)"
        lines = [f"- {name} is a COBOL {info.kind} ({info.loc} code lines)."]
        if info.using:
            lines.append(f"- Parameters (USING): {', '.join(info.using)}.")
        if info.files:
            lines.append(f"- Files: {', '.join(f'{k} -> {v}' for k, v in info.files.items())}.")
        if info.calls:
            lines.append(f"- Calls: {', '.join(info.calls)}.")
        if info.copies:
            lines.append(f"- Copybooks: {', '.join(info.copies)}.")
        paras = [p.name for p in info.paragraphs if p.name != "(MAIN)"]
        if paras:
            lines.append(f"- Paragraphs: {', '.join(paras[:12])}{' ...' if len(paras) > 12 else ''}.")
        if info.features:
            lines.append(f"- Semantics to watch: {', '.join(info.features)}.")
        return "\n".join(lines)

    def do_oracle(self, req: LLMRequest) -> str:
        """Plausible LLM test cases: boundary-style inputs expressed as field values (JSON)."""
        return self._cases(req, "oracle")

    def do_fuzz(self, req: LLMRequest) -> str:
        """Adversarial cases like an LLM reading the translation would propose: values at every
        numeric threshold that appears in the Python code, plus one long input (state, paging)."""
        prompt = "\n".join(m["content"] for m in req.messages)
        code = prompt.split("Current translation:", 1)[-1]
        lits = sorted({Decimal(x) for x in re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])", code)
                       if len(x) < 14})
        return self._cases(req, "fuzz", code_literals=lits, long_case=True)

    def _cases(self, req: LLMRequest, salt: str, code_literals: list | None = None, long_case: bool = False) -> str:
        from .inputs import MAX_LINES, InputGenerator, harvest_literals, property_cases

        name = req.meta["module"]
        n = int(req.meta.get("n", 5))
        lay = self.adapter.layout(name)
        lits = harvest_literals(self.adapter.sources[name])
        if code_literals:
            for rec in lay.records:
                for f in rec.fields:
                    if f.numeric:
                        lits.num_by_field[f.name] = sorted(set(lits.num_by_field.get(f.name, [])) | set(code_literals))
        rng = random.Random(f"mock:{salt}:{name}:{req.meta.get('round', '')}:{len(code_literals or [])}")
        gen = InputGenerator(lay, lits, rng)
        texts = property_cases(gen, n)
        if long_case:
            texts[-1:] = ["\n".join(gen.sequence(MAX_LINES[lay.mode] - 2, lambda rec: gen.record(rec))) + "\n"]
        cases = []
        for i, text in enumerate(texts, 1):
            records = []
            for line in text.rstrip("\n").split("\n"):
                rec = lay.record_for(line)
                if rec is None:
                    continue
                values = {f.key: (str(v) if not isinstance(v, str) else v.rstrip())
                          for f, v in zip(rec.fields, (f.parse(t) for f, t in zip(rec.fields, rec.split(line))))}
                records.append({"record": rec.name, "fields": values})
            if records:
                cases.append({"name": f"mock-{salt}-{i}", "why": "deterministic mock case", "records": records})
        return json.dumps({"cases": cases})

    # ------------------------------------------------------------ translation
    # Offline translations come from <project>/reference_py/<target>.py with catalogued bugs
    # injected, so dry runs exercise verify -> repair -> escalation -> lessons deterministically:
    #   translate: every bug, unless a lesson with the bug's tag is in the prompt
    #   repair:    bugs whose fix needs a stronger model remain ("escalated")
    #   repair_escalated: clean reference, except 'fuzz' bugs not yet shown by a fuzz counterexample
    def do_translate(self, req: LLMRequest) -> str:
        return self._code_reply(req, stage="translate")

    def do_repair(self, req: LLMRequest) -> str:
        return self._code_reply(req, stage="repair_escalated" if req.role == "repair_escalated" else "repair")

    def do_reflect(self, req: LLMRequest) -> str:
        """Lessons for the catalogued bugs whose buggy line was removed by the fix."""
        prompt = "\n".join(m["content"] for m in req.messages)
        lessons = []
        for module in req.meta.get("modules", []):
            for bug in MOCK_BUGS.get(module, []):
                if bug.replace.strip() in _removed_lines(prompt):
                    lessons.append({"text": bug.lesson, "tags": [bug.tag, *bug.features]})
        return json.dumps({"lessons": lessons})

    def _code_reply(self, req: LLMRequest, stage: str) -> str:
        prompt = "\n".join(m["content"] for m in req.messages)
        blocks = []
        for module in req.meta.get("modules", []):
            target = self.adapter.target_name(module)
            ref = self.adapter.root / "reference_py" / f"{target}.py"
            if ref.exists():
                code = ref.read_text()
            else:  # no reference: a plausible but wrong stub (the unit will end up BLOCKED)
                code = f"# module: {module}\n" + _stub(self.adapter, module)
            for bug in MOCK_BUGS.get(module, []):
                applies = (stage == "translate" and f"#{bug.tag}" not in prompt) or \
                          (stage == "repair" and bug.needs == "escalated") or \
                          (stage != "translate" and bug.needs == "fuzz" and "(fuzz_" not in prompt)
                if applies and bug.find in code:
                    code = code.replace(bug.find, bug.replace, 1)
            blocks.append(f"```python\n{code.rstrip()}\n```")
        return "\n\n".join(blocks)


@dataclass(frozen=True)
class MockBug:
    """A realistic translation mistake (find -> replace in the reference translation)."""

    find: str
    replace: str
    tag: str                 # lesson tag that prevents it
    lesson: str              # what the reflector learns after repairing it
    needs: str = "repair"    # 'repair': any repair fixes it | 'escalated': only the stronger tier |
                             # 'fuzz': fixed only once a fuzzer counterexample is in the repair prompt
    features: tuple[str, ...] = ()   # adapter feature tags the lesson is filed under


MOCK_BUGS: dict[str, list[MockBug]] = {
    "FMTUTIL": [MockBug(
        'out = lbrt.alnum(p["FM-OUT"], 20)', 'out = " " * 20', "string-overwrite",
        "STRING ... DELIMITED BY SIZE INTO t only overwrites the characters it writes; keep the rest "
        "of t unless the COBOL code MOVEs SPACES to it first.", features=("string_stmt",))],
    "INTCALC": [MockBug(
        'days360 / 360, 7, 2)', 'days360 / 360, 7, 2, rounded=True)', "truncation",
        "COMPUTE without ROUNDED truncates extra decimals: lbrt.store(v, d, s) with rounded=False.",
        features=("truncation", "rounded"))],
    "STMTRPT": [MockBug(
        'COLUMNS = lbrt.alnum("DATE  DAY DESCRIPTION          AMOUNT            BALANCE", 60)',
        'COLUMNS = "DATE  DAY DESCRIPTION          AMOUNT            BALANCE"', "display-trailing-spaces",
        "DISPLAY prints every byte of the item: pad PIC X(n) values and last fields of group items to "
        "full width (trailing spaces are part of the output).", features=("file_io", "edited_pic")),
        MockBug('if lines == 10:', 'if lines == 11:', "counter-boundary",
                "Reproduce counter limits exactly (page breaks, table sizes): test the counter at the same point "
                "and with the same comparison as the COBOL code, before the next line is printed.",
                needs="fuzz", features=("file_io",))],
    "BALUPD": [
        MockBug('" ", reason)', '" ", reason.rstrip())', "display-trailing-spaces",
                "DISPLAY prints every byte of the item: pad PIC X(n) values and last fields of group items "
                "to full width (trailing spaces are part of the output).", features=("file_io", "edited_pic")),
        MockBug('lbrt.fmt_num(rec_no, 5), " ", rec[1:9]', 'lbrt.fmt_num(rec_no, 5), " ", acct_rec["ACCT-ID"]',
                "record-overlay",
                "Several 01 records under one FD share the same bytes: a field of one record layout reads "
                "the current input line at its columns, even when another record type was read.",
                needs="escalated", features=("file_io",)),
    ],
}


def _removed_lines(prompt: str) -> str:
    """Lines removed in the unified diff of a reflect prompt (without the leading '-')."""
    return "\n".join(ln[1:].strip() for ln in prompt.splitlines() if ln.startswith("-") and not ln.startswith("---"))


def _stub(adapter: "CobolPythonAdapter", module: str) -> str:
    info = adapter.infos[module]
    if info.kind != "subprogram":
        return "import sys\n\n\ndef main() -> int:\n    for _ in sys.stdin:\n        pass\n    return 0\n"
    t = adapter.target_name(module)
    args = ", ".join(f"p{i}" for i in range(len(adapter.layout(module).params)))
    return f"def {t}({args}):\n    return None\n"
