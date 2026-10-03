"""Structured output helpers: JSON extraction, pydantic validation, repair prompts, code fences."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ValidationError

_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


def schema_instruction(schema: type[BaseModel]) -> str:
    """Instruction appended to the system prompt when a schema is requested."""
    return (
        "Respond with ONLY a single JSON object (no prose, no markdown fences) that validates "
        "against this JSON Schema:\n" + json.dumps(schema.model_json_schema(), separators=(",", ":"))
    )


def extract_json(text: str) -> Any:
    """Parse JSON from a model reply, tolerating fences and leading/trailing prose."""
    text = text.strip()
    for candidate in (text, *(m.group(1) for m in _FENCE.finditer(text))):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    # first balanced {...} or [...] block
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        while start != -1:
            depth, in_str, esc = 0, False, False
            for i in range(start, len(text)):
                ch = text[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                elif ch == '"':
                    in_str = True
                elif ch == open_ch:
                    depth += 1
                elif ch == close_ch:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start : i + 1])
                        except json.JSONDecodeError:
                            break
            start = text.find(open_ch, start + 1)
    raise ValueError("no JSON object found in reply")


def parse_structured(text: str, schema: type[BaseModel]) -> BaseModel:
    """Extract and validate; raises ValueError with a concise message on failure."""
    try:
        data = extract_json(text)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        errs = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:6])
        raise ValueError(f"schema validation failed: {errs}") from exc


def repair_message(error: str) -> dict[str, str]:
    """User turn asking the model to fix an invalid structured reply."""
    return {
        "role": "user",
        "content": f"Your previous reply was not valid: {error}. Reply again with ONLY the corrected "
                   "JSON object that matches the schema. No commentary, no markdown fences.",
    }


_CODE_BLOCK = re.compile(r"```(?:python|py)?[ \t]*(?:#[^\n]*)?\n(.*?)```", re.DOTALL)
_MODULE_HDR = re.compile(r"^#\s*module:\s*([A-Za-z0-9_\-]+)\s*$", re.MULTILINE)


def extract_code_blocks(text: str) -> dict[str, str]:
    """Extract python code blocks. Blocks starting with `# module: NAME` are keyed by NAME
    (lower-cased); an unlabeled single block is keyed by '' ."""
    blocks: dict[str, str] = {}
    for m in _CODE_BLOCK.finditer(text):
        body = m.group(1)
        first = next((ln for ln in body.splitlines() if ln.strip()), "")
        hdr = _MODULE_HDR.match(first.strip())
        key = hdr.group(1).lower().replace("-", "_") if hdr else ""
        if key == "" and "" in blocks:
            blocks[""] += "\n" + body
        else:
            blocks[key] = body
    if not blocks and "def " in text and "```" not in text:
        blocks[""] = text
    return blocks
