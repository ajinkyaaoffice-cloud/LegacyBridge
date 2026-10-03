"""Prompt construction with context budgeting for small free-tier context windows / TPM limits.

Agents build a `Prompt` from prioritized sections. The LLM client renders it per model with a
token budget; low-priority sections are first swapped for their compact form, then dropped.
Required sections are never dropped; if they still don't fit, PromptTooLarge is raised and the
client moves to the next model in the chain (e.g. one with a bigger window).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .types import PromptTooLarge


def estimate_tokens(text: str, chars_per_token: float = 3.5) -> int:
    """Cheap, provider-agnostic token estimate (conservative for code)."""
    return int(len(text) / chars_per_token) + 1


@dataclass
class Section:
    name: str
    text: str
    priority: int = 50          # higher = more important (kept longer)
    compact: str | None = None  # cheaper alternative rendering
    required: bool = False


@dataclass
class Prompt:
    system: str
    sections: list[Section] = field(default_factory=list)
    instruction: str = ""      # final user instruction, always kept

    def add(self, name: str, text: str, priority: int = 50, compact: str | None = None,
            required: bool = False) -> "Prompt":
        if text and text.strip():
            self.sections.append(Section(name, text, priority, compact, required))
        return self

    def render(self, budget_tokens: int | None = None, chars_per_token: float = 3.5,
               system_suffix: str = "") -> list[dict[str, str]]:
        """Render to chat messages, shrinking to `budget_tokens` (prompt side) if given."""
        chosen: dict[int, str | None] = {i: s.text for i, s in enumerate(self.sections)}
        system = self.system + (("\n\n" + system_suffix) if system_suffix else "")

        def size() -> int:
            body = "".join(t for t in chosen.values() if t)
            return estimate_tokens(system + body + self.instruction, chars_per_token) + 8 * len(chosen)

        if budget_tokens is not None:
            order = sorted(range(len(self.sections)), key=lambda i: self.sections[i].priority)
            for i in order:  # pass 1: compact forms
                if size() <= budget_tokens:
                    break
                if self.sections[i].compact is not None:
                    chosen[i] = self.sections[i].compact
            for i in order:  # pass 2: drop optional sections
                if size() <= budget_tokens:
                    break
                if not self.sections[i].required:
                    chosen[i] = None
            if size() > budget_tokens:
                raise PromptTooLarge(f"prompt needs ~{size()} tokens, budget {budget_tokens}")

        parts = [f"## {self.sections[i].name}\n{t}" for i, t in chosen.items() if t]
        if self.instruction:
            parts.append(self.instruction)
        return [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(parts)}]


def chunk_text(text: str, max_tokens: int, chars_per_token: float = 3.5,
               boundary: str = "\n") -> list[str]:
    """Split text into chunks of at most ~max_tokens, preferring line boundaries."""
    limit = int(max_tokens * chars_per_token)
    chunks, cur = [], ""
    for line in text.split(boundary):
        piece = line + boundary
        if len(cur) + len(piece) > limit and cur:
            chunks.append(cur)
            cur = ""
        while len(piece) > limit:
            chunks.append(piece[:limit])
            piece = piece[limit:]
        cur += piece
    if cur.strip():
        chunks.append(cur)
    return chunks
