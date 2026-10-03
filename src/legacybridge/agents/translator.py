"""Translator: turn one migration unit (an SCC of programs) into target-language modules.

The prompt carries everything the model needs to be byte-exact: the idiom guide, the generated
interface contract for each member, the interfaces of already-VERIFIED dependencies, lessons from
earlier repairs, the source and a few golden examples. Output is one fenced code block per module,
headed `# module: <name>`.
"""

from __future__ import annotations

from typing import Any

from ..adapters.base import LanguageAdapter
from ..db import Blackboard
from ..llm.prompt import Prompt
from ..llm.structured import extract_code_blocks
from ..llm.types import LLMResult

TRANSLATE_SYSTEM = (
    "You migrate legacy {src} programs to {dst}. Your output is accepted only if it reproduces the "
    "original program's output byte for byte on hundreds of generated test inputs, including invalid "
    "data, overflow, truncation and rounding edge cases. Translate what the code actually does (quirks "
    "included), not what it was meant to do. Reply with code only."
)
TRANSLATE_INSTRUCTION = (
    "Translate {names}. Reply with exactly one ```python fenced block per module, in the order "
    "{targets}; the first line of each block must be `# module: <module name>`. Complete modules only "
    "(no placeholders, no explanations outside the code)."
)


def executable_members(db: Blackboard, members: list[str]) -> list[str]:
    """Members that are translated (copybooks are data-only and folded into their users)."""
    return [m for m in members if (db.module(m) or {}).get("kind") != "copybook"]


def external_deps(db: Blackboard, members: list[str]) -> list[str]:
    """Modules directly called by the unit that live in other (already verified) units."""
    out: list[str] = []
    for e in db.edges():
        if e["kind"] == "call" and e["src"] in members and e["dst"] not in members and e["dst"] not in out:
            out.append(e["dst"])
    return out


def format_lessons(lessons: list[dict[str, Any]]) -> str:
    """Lessons rendered for a prompt: '- (#tag #tag) text'."""
    return "\n".join(f"- ({' '.join('#' + t for t in les.get('tags', []))}) {les['text']}" for les in lessons)


def sample_tests(db: Blackboard, module: str, k: int = 2, max_chars: int = 1200) -> str:
    """A couple of golden examples (input -> exact output) to anchor formats."""
    tests = db.test_cases(module, origins=["property", "llm", "random"])
    picked = sorted(tests, key=lambda t: len(t["input"]) + len(t["expected_stdout"]))[:k]
    parts = []
    for t in picked:
        out = t["expected_stdout"]
        if len(out) > max_chars:
            out = out[:max_chars] + "...(truncated)"
        parts.append(f"stdin:\n{t['input'].rstrip()}\nexpected stdout (exact):\n{out.rstrip()}\n"
                     f"expected exit code: {t['expected_exit']}")
    return "\n\n".join(parts)


def base_prompt(adapter: LanguageAdapter, db: Blackboard, members: list[str], dep_code: dict[str, str],
                lessons: list[dict[str, Any]], system: str, instruction: str) -> Prompt:
    """Sections shared by translate and repair prompts."""
    p = Prompt(system, instruction=instruction)
    p.add("Idiom guide", adapter.idiom_guide(), priority=70, compact=adapter.compact_idiom_guide(), required=True)
    for m in members:
        p.add(f"Interface contract for {m}", adapter.interface_contract(m), priority=96, required=True)
    deps = external_deps(db, members)
    if deps:
        text = "\n\n".join(adapter.dependency_interface(d, dep_code.get(adapter.target_name(d), "")) for d in deps)
        p.add("Already-migrated modules you can call (verified; do not re-implement them)", text,
              priority=92, required=True)
    if lessons:
        p.add("Lessons learned from earlier repairs (apply them)", format_lessons(lessons), priority=75)
    for m in members:
        summary = (db.module(m) or {}).get("summary")
        if summary:
            p.add(f"Behavior summary of {m}", summary, priority=35)
    return p


def collect_code(adapter: LanguageAdapter, members: list[str], text: str,
                 previous: dict[str, str] | None = None) -> dict[str, str]:
    """Map the reply's code blocks to target module names. Missing blocks fall back to `previous`."""
    blocks = extract_code_blocks(text)
    out: dict[str, str] = {}
    for m in members:
        t = adapter.target_name(m)
        code = blocks.get(t) or blocks.get(m.lower().replace("-", "_"))
        if code is None and len(members) == 1 and "" in blocks:
            code = blocks[""]
        if code is None and previous and t in previous:
            code = previous[t]
        if code is not None:
            out[t] = code
    return out


async def translate_unit(adapter: LanguageAdapter, db: Blackboard, llm, unit: str, members: list[str], *,
                         dep_code: dict[str, str], lessons: list[dict[str, Any]], attempt: int = 1,
                         role: str = "translator") -> tuple[dict[str, str], LLMResult]:
    """First translation of a unit's executable members. Returns ({target: code}, llm result)."""
    targets = [adapter.target_name(m) for m in members]
    p = base_prompt(adapter, db, members, dep_code, lessons,
                    TRANSLATE_SYSTEM.format(src=adapter.source_language, dst=adapter.target_language),
                    TRANSLATE_INSTRUCTION.format(names=", ".join(members), targets=", ".join(targets)))
    for m in members:
        ctx = adapter.module_context(m)
        p.add(f"Source of {m}", ctx, priority=95, required=True)
        p.add(f"Golden examples for {m} (recorded from the original program)", sample_tests(db, m), priority=45,
              compact=sample_tests(db, m, k=1, max_chars=400))
    res = await llm.complete(role, p, None, unit=unit,
                             meta={"task": "translate", "unit": unit, "modules": members, "attempt": attempt})
    return collect_code(adapter, members, res.text), res
