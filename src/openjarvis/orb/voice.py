"""Voice loop for the floating Jarvis orb.

Waits for the "Hey Jarvis" wake word (openWakeWord, fully local), records the
request until the user pauses, sends it to the running Jarvis server, and
speaks the answer. After answering it keeps listening briefly for a follow-up,
so a conversation does not need the wake word on every turn.
"""

from __future__ import annotations

import io
import json
import os
import queue
import re
import threading
import wave
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator, List, Optional

import numpy as np

SAMPLE_RATE = 16_000
FRAME = 1280  # 80 ms, the frame size openWakeWord expects

VOICE_PROMPT = (
    "You are Jarvis, speaking out loud. Reply in one to three short, natural "
    "sentences, without markdown, lists, or code, unless asked for detail."
)

_CLAUDE = re.compile(
    r"^\s*(?:(?:hey|hi|ok|okay)[\s,]+)?(?:jarvis[\s,.]+)?(?:ask\s+)?"
    r"(?:claude|cloud|clod)\b[\s,.:;!?-]*(.*)$",
    re.IGNORECASE | re.DOTALL,
)
_BRAINSTORM = re.compile(
    r"^\s*(?:(?:hey|ok|okay)[\s,]+)?(?:jarvis[\s,.]+)?"
    r"(?:(?:let'?s|please|can you|could you)\s+)?brainstorm\b"
    r"(?:\s+with\s+(?:claude|cloud|clod)\b)?(?:\s+(?:about|on|for))?"
    r"[\s,.:;!?-]*(.*)$",
    re.IGNORECASE | re.DOTALL,
)


def parse_brainstorm(text: str) -> Optional[str]:
    """Topic of a "brainstorm with Claude about ..." request, else None."""
    match = _BRAINSTORM.match(text)
    if not match:
        return None
    topic = match.group(1).strip().rstrip(".!?")
    return topic or None


def parse_claude(text: str) -> Optional[str]:
    """Task of a "Claude, ..." request, else None."""
    match = _CLAUDE.match(text)
    if not match:
        return None
    task = match.group(1).strip()
    return task or None


class Endpointer:
    """Decide when a spoken request has ended (same rules as the web UI)."""

    def __init__(
        self,
        threshold: float = 0.015,
        silence_s: float = 0.7,
        min_speech_s: float = 0.25,
        max_s: float = 30.0,
        idle_s: float = 8.0,
    ) -> None:
        self.threshold = threshold
        self.silence_s = silence_s
        self.min_speech_s = min_speech_s
        self.max_s = max_s
        self.idle_s = idle_s
        self.t = 0.0
        self.speech_start: Optional[float] = None
        self.last_loud: Optional[float] = None
        self.heard = False

    def push(self, rms: float, dt: float) -> str:
        """Feed one frame's loudness; returns waiting/speaking/done/idle."""
        self.t += dt
        if rms >= self.threshold:
            self.last_loud = self.t
            if self.speech_start is None:
                self.speech_start = self.t
            if self.t - self.speech_start >= self.min_speech_s:
                self.heard = True
        elif (
            not self.heard
            and self.last_loud is not None
            and self.t - self.last_loud >= self.silence_s
        ):
            self.speech_start = None
            self.last_loud = None
        if not self.heard:
            return "idle" if self.t >= self.idle_s else "waiting"
        if self.t - (self.speech_start or 0.0) >= self.max_s:
            return "done"
        if self.last_loud is not None and self.t - self.last_loud >= self.silence_s:
            return "done"
        return "speaking"


def frame_rms(frame: np.ndarray) -> float:
    if frame.size == 0:
        return 0.0
    x = frame.astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(x * x)))


def to_wav(samples: np.ndarray, rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.astype(np.int16).tobytes())
    return buf.getvalue()


class JarvisClient:
    """Thin HTTP client for the local Jarvis server."""

    def __init__(self, base_url: str, model: str) -> None:
        import httpx

        # trust_env=False: a system proxy must never sit between us and localhost.
        self._http = httpx.Client(base_url=base_url, timeout=600, trust_env=False)
        self.model = model

    def transcribe(self, wav: bytes) -> str:
        r = self._http.post(
            "/v1/speech/transcribe", files={"file": ("speech.wav", wav, "audio/wav")}
        )
        r.raise_for_status()
        return str(r.json().get("text", "")).strip()

    def chat(self, history: List[dict]) -> str:
        r = self._http.post(
            "/v1/chat/completions",
            json={
                "model": self.model,
                "messages": [{"role": "system", "content": VOICE_PROMPT}, *history],
                "max_tokens": 300,
                "stream": False,
            },
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    def chat_stream(self, history: List[dict]) -> Iterator[str]:
        """Yield reply text as the local model writes it."""
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": VOICE_PROMPT}, *history],
            "max_tokens": 300,
            "stream": True,
        }
        with self._http.stream("POST", "/v1/chat/completions", json=body) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                choices = chunk.get("choices") or [{}]
                delta = choices[0].get("delta", {}).get("content")
                if delta:
                    yield delta

    def ask_claude(self, task: str) -> str:
        r = self._http.post("/v1/agents/claude/ask", json={"message": task})
        r.raise_for_status()
        return r.json().get("content", "")

    def brainstorm(self, topic: str) -> str:
        r = self._http.post(
            "/v1/agents/brainstorm",
            json={"topic": topic, "rounds": 2, "model": self.model},
        )
        r.raise_for_status()
        return (
            r.json().get("summary", "")
            + " The full conversation is in your Jarvis Brainstorms folder."
        )

    def synthesize(self, text: str) -> bytes:
        r = self._http.post("/v1/speech/synthesize", json={"text": text})
        r.raise_for_status()
        return r.content


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def split_sentences(chunks: Iterable[str], min_len: int = 24) -> Iterator[str]:
    """Regroup streamed text into speakable sentences as soon as each ends.

    Very short sentences are merged with the next one so playback is not a
    string of tiny clips.
    """
    buf = ""
    for chunk in chunks:
        buf += chunk
        while True:
            parts = _SENTENCE_END.split(buf, maxsplit=1)
            if len(parts) < 2:
                break
            head, rest = parts
            if len(head) < min_len and rest:
                # Too short to say alone: wait for the next sentence too.
                nxt = _SENTENCE_END.split(rest, maxsplit=1)
                if len(nxt) < 2:
                    break
                head, rest = head + " " + nxt[0], nxt[1]
            yield head.strip()
            buf = rest
    if buf.strip():
        yield buf.strip()


def _speakable(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"[*_`#>]+", "", text)
    return text.strip()


@dataclass
class OrbState:
    phase: str = "idle"  # idle, listening, thinking, speaking, muted, error
    level: float = 0.0
    message: str = ""
    # Live caption shown beside the orb: what the user said, or Jarvis's reply.
    caption: str = ""
    speaker: str = ""  # "you", "jarvis", or ""
    history: List[dict] = field(default_factory=list)


class VoiceLoop(threading.Thread):
    """Background thread: wake word → record → ask Jarvis → speak → follow-up."""

    FOLLOW_UP_S = 6.0
    PARTIAL_EVERY_S = 1.0
    WAKE_THRESHOLD = 0.5

    def __init__(
        self,
        client: JarvisClient,
        on_change: Callable[[OrbState], None],
    ) -> None:
        super().__init__(daemon=True)
        self.client = client
        self.on_change = on_change
        self.state = OrbState()
        self._frames: "queue.Queue[np.ndarray]" = queue.Queue()
        self._muted = threading.Event()
        self._manual = threading.Event()
        self._interrupt = threading.Event()
        self._stop = threading.Event()
        self._turn: Optional[object] = None

    # -- controls (called from the UI thread) -------------------------------
    def tap(self) -> None:
        """Click on the orb: talk now, or cut off a reply mid-sentence."""
        if self.state.phase == "speaking":
            self._interrupt.set()
        elif self.state.phase in ("idle", "error"):
            self._manual.set()

    def set_muted(self, muted: bool) -> None:
        (self._muted.set if muted else self._muted.clear)()
        self._set("muted" if muted else "idle")

    def stop(self) -> None:
        self._stop.set()

    # -- internals -----------------------------------------------------------
    def _set(
        self,
        phase: str,
        level: float = 0.0,
        message: str = "",
        caption: Optional[str] = None,
        speaker: Optional[str] = None,
    ) -> None:
        self.state.phase = phase
        self.state.level = level
        self.state.message = message
        if caption is not None:
            self.state.caption = caption
        if speaker is not None:
            self.state.speaker = speaker
        self.on_change(self.state)

    def _caption(self, speaker: str, text: str) -> None:
        self._set(self.state.phase, self.state.level, caption=text, speaker=speaker)

    def _drain(self) -> None:
        while not self._frames.empty():
            try:
                self._frames.get_nowait()
            except queue.Empty:
                break

    def run(self) -> None:
        import sounddevice as sd
        from openwakeword.model import Model

        try:
            wake = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        except (ValueError, FileNotFoundError):
            # First run: fetch the wake-word model (about 2 MB, from GitHub).
            from openwakeword.utils import download_models

            download_models(["hey_jarvis"])
            wake = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")

        def callback(indata, _frames, _time, _status) -> None:
            self._frames.put(indata[:, 0].copy())

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME,
            callback=callback,
        ):
            self._set("idle")
            while not self._stop.is_set():
                try:
                    frame = self._frames.get(timeout=0.2)
                except queue.Empty:
                    continue
                if self._muted.is_set():
                    continue
                woke = self._manual.is_set()
                if not woke:
                    score = wake.predict(frame).get("hey_jarvis", 0.0)
                    woke = score >= self.WAKE_THRESHOLD
                if not woke:
                    continue
                self._manual.clear()
                wake.reset()
                self.state.history.clear()
                self._conversation(idle_s=8.0)
                self._drain()
                wake.reset()
                if not self._muted.is_set():
                    self._set("idle", caption="", speaker="")

    def _conversation(self, idle_s: float) -> None:
        """Answer turns until the user stops talking."""
        while not self._stop.is_set() and not self._muted.is_set():
            audio = self._record(idle_s)
            if audio is None:
                return
            self._set("thinking")
            try:
                text = self.client.transcribe(to_wav(audio))
                if not text:
                    return
                self._set("thinking", caption=text, speaker="you")
                self._answer_aloud(text)
            except Exception as exc:  # network, server, or audio failure
                self._set(
                    "error",
                    message=str(exc)[:200],
                    caption="Couldn't reach Jarvis. Is the server running?",
                    speaker="",
                )
                return
            self._drain()
            idle_s = self.FOLLOW_UP_S

    def _record(self, idle_s: float) -> Optional[np.ndarray]:
        self._drain()
        self._set("listening", caption="", speaker="you")
        ep = Endpointer(idle_s=idle_s)
        chunks: List[np.ndarray] = []
        busy = threading.Event()  # one partial transcription in flight at a time
        since_partial = 0.0
        turn = object()
        self._turn = turn

        def partial(audio: np.ndarray) -> None:
            try:
                text = self.client.transcribe(to_wav(audio))
                # Ignore late results once this recording has ended.
                if text and self._turn is turn and self.state.phase == "listening":
                    self._caption("you", text)
            except Exception:
                pass  # live captions are best effort
            finally:
                busy.clear()

        while not self._stop.is_set():
            try:
                frame = self._frames.get(timeout=1.0)
            except queue.Empty:
                continue
            rms = frame_rms(frame)
            chunks.append(frame)
            self._set("listening", level=min(1.0, rms * 10))
            decision = ep.push(rms, FRAME / SAMPLE_RATE)
            since_partial += FRAME / SAMPLE_RATE
            if decision in ("done", "idle"):
                self._turn = None
                return np.concatenate(chunks) if decision == "done" else None
            if ep.heard and since_partial >= self.PARTIAL_EVERY_S and not busy.is_set():
                busy.set()
                since_partial = 0.0
                snapshot = np.concatenate(chunks)
                threading.Thread(target=partial, args=(snapshot,), daemon=True).start()
        return None

    def _reply_chunks(self, text: str) -> Iterator[str]:
        topic = parse_brainstorm(text)
        if topic:
            yield self.client.brainstorm(topic)
            return
        task = parse_claude(text)
        if task:
            yield self.client.ask_claude(task)
            return
        self.state.history.append({"role": "user", "content": text})
        reply = ""
        for delta in self.client.chat_stream(self.state.history[-8:]):
            reply += delta
            yield delta
        self.state.history.append({"role": "assistant", "content": reply})

    def _answer_aloud(self, text: str) -> None:
        """Speak the reply sentence by sentence while the rest is prepared."""
        import sounddevice as sd
        import soundfile as sf

        clips: "queue.Queue[object]" = queue.Queue(maxsize=4)
        failure: List[BaseException] = []
        self._interrupt.clear()

        def produce() -> None:
            try:
                for sentence in split_sentences(self._reply_chunks(text)):
                    if self._interrupt.is_set():
                        break
                    spoken = _speakable(sentence)
                    if not spoken:
                        continue
                    wav = self.client.synthesize(spoken)
                    data, rate = sf.read(io.BytesIO(wav), dtype="float32")
                    clips.put((data, rate, spoken))
            except BaseException as exc:  # surfaced on the voice thread below
                failure.append(exc)
            finally:
                clips.put(None)

        threading.Thread(target=produce, daemon=True).start()
        said = ""
        while True:
            clip = clips.get()
            if clip is None or self._interrupt.is_set():
                break
            data, rate, sentence = clip
            said = f"{said} {sentence}".strip()
            # Keep the bubble short: show the tail of what has been said.
            shown = said if len(said) <= 220 else "…" + said[-220:]
            self._set("speaking", caption=shown, speaker="jarvis")
            sd.play(data, rate)
            deadline = len(data) / rate + 0.2
            waited = 0.0
            while waited < deadline:
                if self._interrupt.wait(0.05):
                    break
                waited += 0.05
            sd.stop()
        if failure and not self._interrupt.is_set():
            raise failure[0]


def default_client() -> JarvisClient:
    return JarvisClient(
        os.environ.get("JARVIS_ORB_URL", "http://127.0.0.1:8000"),
        os.environ.get("JARVIS_ORB_MODEL", "qwen3.5:2b"),
    )
