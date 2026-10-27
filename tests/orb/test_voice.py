import numpy as np
import pytest

pytest.importorskip("numpy")

from openjarvis.orb.voice import (  # noqa: E402
    Endpointer,
    frame_rms,
    parse_brainstorm,
    parse_claude,
)


def test_parse_brainstorm():
    assert parse_brainstorm("Jarvis, brainstorm with Claude about apps.") == "apps"
    assert parse_brainstorm("let's brainstorm side hustles") == "side hustles"
    assert parse_brainstorm("what is a brainstorm") is None


def test_parse_claude():
    assert parse_claude("Claude, check my Downloads") == "check my Downloads"
    assert parse_claude("Jarvis ask Claude what's new") == "what's new"
    assert parse_claude("Cloudy today?") is None


def test_endpointer_ends_after_pause():
    ep = Endpointer(threshold=0.1, silence_s=1.0, min_speech_s=0.2, idle_s=5)
    out = [ep.push(0.5, 0.1) for _ in range(5)]
    out += [ep.push(0.0, 0.1) for _ in range(11)]
    assert out[0] == "waiting"
    assert "speaking" in out
    assert out[-1] == "done"


def test_endpointer_gives_up_when_silent():
    ep = Endpointer(idle_s=1.0)
    out = [ep.push(0.0, 0.1) for _ in range(11)]
    assert out[-1] == "idle"


def test_frame_rms():
    assert frame_rms(np.zeros(10, dtype=np.int16)) == 0.0
    loud = np.full(10, 32767, dtype=np.int16)
    assert frame_rms(loud) > 0.99


def test_split_sentences_streams_and_merges_short_ones():
    from openjarvis.orb.voice import split_sentences

    chunks = ["Hi. It is ", "eight in the morning in Tokyo. ", "Have a ", "nice day!"]
    out = list(split_sentences(chunks))
    assert out == ["Hi. It is eight in the morning in Tokyo.", "Have a nice day!"]


def test_split_sentences_flushes_tail_without_punctuation():
    from openjarvis.orb.voice import split_sentences

    assert list(split_sentences(["no punctuation here"])) == ["no punctuation here"]
