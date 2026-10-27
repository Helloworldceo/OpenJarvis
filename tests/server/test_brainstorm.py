from datetime import datetime

from openjarvis.server.brainstorm import run_brainstorm, save_report


def test_alternates_and_summarizes():
    calls = []

    def jarvis(system, user):
        calls.append(("jarvis", user))
        return f"jarvis {len(calls)}"

    def claude(prompt):
        calls.append(("claude", prompt))
        return f"claude {len(calls)}"

    result = run_brainstorm("weekend app", 2, jarvis, claude)

    assert [who for who, _ in result.turns] == ["Jarvis", "Claude", "Jarvis", "Claude"]
    expected = ["jarvis", "claude", "jarvis", "claude", "jarvis"]
    assert [who for who, _ in calls] == expected
    # Claude has no memory between turns, so it must see the whole transcript.
    assert "Jarvis: jarvis 1" in calls[3][1]
    assert "Claude: claude 2" in calls[3][1]
    assert result.summary == "jarvis 5"


def test_save_report(tmp_path):
    result = run_brainstorm(
        "Home / Garden?", 1, lambda s, u: "idea", lambda p: "critique"
    )
    path = save_report(result, tmp_path, datetime(2026, 10, 27, 19, 45))

    assert path.name == "2026-10-27-1945-home-garden.md"
    text = path.read_text()
    assert "# Brainstorm: Home / Garden?" in text
    assert "**Claude:** critique" in text
    assert result.report_path == str(path)
