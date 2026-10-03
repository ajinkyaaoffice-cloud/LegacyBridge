"""Reflector: after a unit needed repairs to pass, distill reusable lessons for later units.

Lessons are short, general rules ("COMPUTE without ROUNDED truncates...") tagged with the semantic
features they concern (the adapter's feature tags, e.g. `rounded`, `string_stmt`, `file_io`).
Later translation prompts include the lessons whose tags best match the unit's features.
"""

from __future__ import annotations

import difflib
from typing import Any

from pydantic import BaseModel, Field

from ..adapters.base import LanguageAdapter
from ..db import Blackboard
from ..llm.prompt import Prompt

REFLECT_SYSTEM = (
    "You maintain a short list of lessons for an LLM that translates legacy {src} to {dst}. Given a "
    "translation that failed differential tests and the repaired version that passed, state the "
    "GENERAL rule that was violated, so the mistake is not repeated on other programs."
)
REFLECT_INSTRUCTION = (
    "Return 1-2 lessons. Each lesson: one or two sentences, general (no program, field or paragraph "
    "names), actionable. Tags: pick from the feature tags listed (plus at most one short new tag)."
)


class LessonOut(BaseModel):
    text: str
    tags: list[str] = Field(default_factory=list)


class LessonsOut(BaseModel):
    lessons: list[LessonOut] = Field(default_factory=list)


def select_lessons(db: Blackboard, adapter: LanguageAdapter, members: list[str], k: int) -> list[dict[str, Any]]:
    """Up to k lessons, best tag overlap with the members' features first, then most used, newest."""
    feats = {f for m in members for f in adapter.features(m)}
    lessons = db.lessons()
    ranked = sorted(lessons, key=lambda les: (-len(feats & set(les["tags"])), -les["uses"], -les["id"]))
    return ranked[:k]


def _diff(old: str, new: str, limit: int = 4000) -> str:
    d = "\n".join(difflib.unified_diff(old.splitlines(), new.splitlines(), "failing", "fixed", lineterm="", n=2))
    return d[:limit] + ("\n...(truncated)" if len(d) > limit else "")


async def reflect(adapter: LanguageAdapter, db: Blackboard, llm, unit: str, members: list[str]) -> list[int]:
    """Create lessons from the unit's last failing -> passing transition. Returns new lesson ids."""
    attempts = db.attempts(unit)
    passing = next((a for a in reversed(attempts) if a["verdict"] == "pass"), None)
    failing = next((a for a in reversed(attempts) if a["verdict"] in ("fail", "error")
                    and passing and a["id"] < passing["id"]), None)
    if not passing or not failing:
        return []
    feats = sorted({f for m in members for f in adapter.features(m)})
    p = Prompt(REFLECT_SYSTEM.format(src=adapter.source_language, dst=adapter.target_language),
               instruction=REFLECT_INSTRUCTION)
    p.add("Feature tags", ", ".join(feats), priority=90, required=True)
    p.add("What failed", _summarize_failure(failing["diff"]), priority=85, required=True)
    changes = "\n\n".join(_diff(failing["code"].get(t, ""), passing["code"].get(t, ""))
                          for t in passing["code"] if failing["code"].get(t) != passing["code"].get(t))
    p.add("Fix (diff failing -> passing)", changes or "(no code change)", priority=80, required=True)
    p.add("Failing code", "\n\n".join(failing["code"].values())[:8000], priority=30)
    p.add("Existing lessons (do not repeat)", "\n".join(f"- {les['text']}" for les in db.lessons()), priority=60)
    res = await llm.complete("reflector", p, LessonsOut, unit=unit,
                             meta={"task": "reflect", "unit": unit, "modules": members})
    existing = {les["text"].strip().lower() for les in db.lessons()}
    ids = []
    for les in res.parsed.lessons[:2]:
        text = " ".join(les.text.split())
        if len(text) < 15 or text.lower() in existing:
            continue
        tags = [t.strip().lower().replace(" ", "_") for t in les.tags if t.strip()][:5]
        lid = db.add_lesson(text, tags, unit)
        ids.append(lid)
        existing.add(text.lower())
        db.emit("lesson_added", unit, id=lid, text=text, tags=tags)
    return ids


def _summarize_failure(diff: Any) -> str:
    if not isinstance(diff, dict):
        return str(diff)[:2000]
    parts = []
    for module, s in diff.items():
        if not isinstance(s, dict):
            continue
        line = f"{module}: {s.get('passed')}/{s.get('total')} tests passed"
        if s.get("static_error"):
            line += f"; static error: {s['static_error']}"
        ff = s.get("first_failure") or {}
        if ff.get("expected") is not None:
            line += f"\n  expected: {ff.get('expected')!r}\n  actual:   {ff.get('actual')!r}"
        if ff.get("fields"):
            line += "\n  " + str(ff["fields"])[:600]
        parts.append(line)
    return "\n".join(parts)[:3000]
