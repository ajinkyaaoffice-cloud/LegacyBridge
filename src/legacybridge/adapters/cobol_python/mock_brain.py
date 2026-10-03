"""Deterministic "brain" for MockProvider in offline runs (`--dry-run` and tests).

It dispatches on `req.meta["task"]` and answers from the parser output (summaries) and, for
translation tasks, from hand-written reference translations in `<project>/reference_py/`.
Real runs never use this module.
"""

from __future__ import annotations

import json
import random
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
        return self._cases(req, "fuzz")

    def _cases(self, req: LLMRequest, salt: str) -> str:
        from .inputs import InputGenerator, harvest_literals, property_cases

        name = req.meta["module"]
        n = int(req.meta.get("n", 5))
        lay = self.adapter.layout(name)
        gen = InputGenerator(lay, harvest_literals(self.adapter.sources[name]), random.Random(f"mock:{salt}:{name}"))
        cases = []
        for i, text in enumerate(property_cases(gen, n), 1):
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
