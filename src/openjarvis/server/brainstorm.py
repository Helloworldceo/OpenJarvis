"""Jarvis x Claude brainstorm: two agents trade ideas, Jarvis reports back.

Jarvis (the local model) pitches and refines; Claude (the Claude Code agent,
which can look at the user's files) critiques and builds on each pitch. After
a few rounds Jarvis writes a short spoken summary, and the full exchange is
saved as a Markdown report.

The loop takes plain callables so it can be tested without a model or Claude.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Tuple

JARVIS_ROLE = (
    "You are Jarvis, a personal AI assistant brainstorming project ideas with "
    "Claude, a coding assistant that can see the user's files. Pitch or refine "
    "ONE concrete idea per turn in at most four sentences. Build on Claude's "
    "feedback instead of repeating yourself."
)

CLAUDE_ROLE = (
    "You are brainstorming project ideas with Jarvis, the user's local AI "
    "assistant. Look at the user's home folder (for example Documents, "
    "Projects, code folders) if it helps you judge what fits their skills and "
    "interests. Critique Jarvis's latest idea and add one concrete improvement "
    "or alternative, in at most five sentences. Do not modify any files."
)

SUMMARY_ROLE = (
    "You are Jarvis reporting back to the user after brainstorming with Claude. "
    "In three to five short spoken sentences with no markdown, name the single "
    "best project idea, say why it fits the user, and give the first step."
)

Turn = Tuple[str, str]  # (speaker, text)


@dataclass
class BrainstormResult:
    topic: str
    turns: List[Turn] = field(default_factory=list)
    summary: str = ""
    report_path: str = ""


def _transcript(turns: List[Turn]) -> str:
    return "\n\n".join(f"{who}: {text}" for who, text in turns)


def run_brainstorm(
    topic: str,
    rounds: int,
    jarvis: Callable[[str, str], str],
    claude: Callable[[str], str],
) -> BrainstormResult:
    """Alternate Jarvis and Claude for *rounds* rounds, then summarize.

    ``jarvis(system, user)`` calls the local model; ``claude(prompt)`` calls the
    Claude Code agent. Claude starts each turn without memory, so it always
    receives the full transcript so far.
    """
    result = BrainstormResult(topic=topic)
    for _ in range(max(1, rounds)):
        so_far = _transcript(result.turns)
        jarvis_prompt = (
            f"Brainstorm topic: {topic}\n\n"
            + (f"Conversation so far:\n{so_far}\n\n" if so_far else "")
            + "Your turn, Jarvis."
        )
        result.turns.append(("Jarvis", jarvis(JARVIS_ROLE, jarvis_prompt).strip()))

        claude_prompt = (
            f"{CLAUDE_ROLE}\n\nBrainstorm topic: {topic}\n\n"
            f"Conversation so far:\n{_transcript(result.turns)}\n\nYour turn, Claude."
        )
        result.turns.append(("Claude", claude(claude_prompt).strip()))

    result.summary = jarvis(
        SUMMARY_ROLE,
        f"Brainstorm topic: {topic}\n\nFull conversation:\n{_transcript(result.turns)}",
    ).strip()
    return result


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:50] or "brainstorm"


def save_report(result: BrainstormResult, folder: Path, now: datetime) -> Path:
    """Write the brainstorm as Markdown into *folder* and return the file path."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{now:%Y-%m-%d-%H%M}-{_slug(result.topic)}.md"
    lines = [
        f"# Brainstorm: {result.topic}",
        "",
        f"_{now:%Y-%m-%d %H:%M} — Jarvis × Claude_",
        "",
        "## Jarvis's report",
        "",
        result.summary,
        "",
        "## Conversation",
        "",
    ]
    for who, text in result.turns:
        lines += [f"**{who}:** {text}", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    result.report_path = str(path)
    return path
