import { useCallback, useEffect, useRef, useState } from 'react';
import { transcribeAudio } from '../lib/api';
import { useAppStore } from '../lib/store';
import { useTtsStore } from '../lib/tts';
import { stripThinkTags } from '../lib/message-text';
import { createSilenceDetector, rmsFromTimeDomain } from '../lib/voice-activity';

export type ConversationPhase = 'off' | 'listening' | 'transcribing' | 'thinking' | 'speaking';

interface Options {
  /** Send one spoken turn; resolves once the assistant reply has finished streaming. */
  onUtterance: (text: string) => Promise<void>;
}

const SAMPLE_MS = 50;

/**
 * Hands-free voice loop: listen → send when the user pauses → speak the reply
 * → listen again, until stopped.
 *
 * The microphone stream stays open for the whole session, but audio is only
 * recorded while listening, so the assistant's own voice is never captured and
 * sent back as a user turn.
 */
export function useConversationMode({ onUtterance }: Options) {
  const [phase, setPhase] = useState<ConversationPhase>('off');
  const [level, setLevel] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const onUtteranceRef = useRef(onUtterance);
  onUtteranceRef.current = onUtterance;

  // Bumped on every stop so stale async steps from a finished session bail out.
  const sessionRef = useRef(0);
  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const ttsUnsubRef = useRef<(() => void) | null>(null);

  const clearTimer = () => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  };

  const teardown = useCallback(() => {
    clearTimer();
    ttsUnsubRef.current?.();
    ttsUnsubRef.current = null;
    const rec = recorderRef.current;
    recorderRef.current = null;
    if (rec && rec.state !== 'inactive') {
      rec.ondataavailable = null;
      rec.onstop = null;
      rec.stop();
    }
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    analyserRef.current = null;
    void audioCtxRef.current?.close().catch(() => {});
    audioCtxRef.current = null;
    setLevel(0);
  }, []);

  const stop = useCallback(() => {
    sessionRef.current += 1;
    teardown();
    useTtsStore.getState().stop();
    setPhase('off');
  }, [teardown]);

  // listen and speakReply call each other; refs break the useCallback cycle.
  const listenRef = useRef<(session: number) => void>(() => {});

  const speakReply = useCallback(async (session: number) => {
    const app = useAppStore.getState();
    const last = app.messages[app.messages.length - 1];
    const tts = useTtsStore.getState();
    await tts.ensureHealth();
    if (session !== sessionRef.current) return;

    const text = last?.role === 'assistant' ? stripThinkTags(last.content) : '';
    if (!text || useTtsStore.getState().available !== true) {
      listenRef.current(session);
      return;
    }

    setPhase('speaking');
    // Claim the reply first so the regular autoplay path cannot read it twice.
    tts.markAutoSpoken(last.id);
    // Resume listening as soon as playback ends (or fails, or is interrupted).
    let started = false;
    ttsUnsubRef.current?.();
    ttsUnsubRef.current = useTtsStore.subscribe((s) => {
      if (s.state !== 'idle') started = true;
      if (started && s.state === 'idle') {
        ttsUnsubRef.current?.();
        ttsUnsubRef.current = null;
        if (session === sessionRef.current) listenRef.current(session);
      }
    });
    await tts.speak(last.id, text);
    // speak() returns without ever leaving idle when synthesis is skipped.
    if (!started && useTtsStore.getState().state === 'idle' && session === sessionRef.current) {
      ttsUnsubRef.current?.();
      ttsUnsubRef.current = null;
      listenRef.current(session);
    }
  }, []);

  const finishTurn = useCallback(async (session: number, blob: Blob) => {
    setPhase('transcribing');
    setLevel(0);
    let text = '';
    try {
      text = (await transcribeAudio(blob)).text.trim();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Transcription failed');
    }
    if (session !== sessionRef.current) return;
    if (!text) {
      listenRef.current(session);
      return;
    }
    setPhase('thinking');
    try {
      await onUtteranceRef.current(text);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sending failed');
    }
    if (session !== sessionRef.current) return;
    await speakReply(session);
  }, [speakReply]);

  const listen = useCallback((session: number) => {
    const stream = streamRef.current;
    const analyser = analyserRef.current;
    if (!stream || !analyser || session !== sessionRef.current) return;

    const recorder = new MediaRecorder(stream);
    const chunks: Blob[] = [];
    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunks.push(e.data);
    };
    recorder.onstop = () => {
      if (session !== sessionRef.current) return;
      const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
      void finishTurn(session, blob);
    };
    recorderRef.current = recorder;
    recorder.start();
    setPhase('listening');

    const frame = new Uint8Array(analyser.fftSize);
    const vad = createSilenceDetector(performance.now());
    clearTimer();
    timerRef.current = setInterval(() => {
      analyser.getByteTimeDomainData(frame);
      const rms = rmsFromTimeDomain(frame);
      setLevel(Math.min(1, rms * 8));
      const decision = vad.push(rms, performance.now());
      if (decision === 'done') {
        clearTimer();
        recorderRef.current = null;
        recorder.stop();
      } else if (decision === 'idle-timeout') {
        // Nobody spoke: drop this (silent) recording and start a fresh one so
        // the buffer never grows without bound.
        clearTimer();
        recorderRef.current = null;
        recorder.onstop = null;
        recorder.stop();
        listenRef.current(session);
      }
    }, SAMPLE_MS);
  }, [finishTurn]);
  listenRef.current = listen;

  const start = useCallback(async () => {
    if (phase !== 'off') return;
    setError(null);
    if (!navigator.mediaDevices?.getUserMedia) {
      setError('Microphone not supported in this browser');
      return;
    }
    const session = ++sessionRef.current;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
      if (session !== sessionRef.current) {
        stream.getTracks().forEach((t) => t.stop());
        return;
      }
      const ctx = new AudioContext();
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 1024;
      ctx.createMediaStreamSource(stream).connect(analyser);
      streamRef.current = stream;
      audioCtxRef.current = ctx;
      analyserRef.current = analyser;
      // Also unlocks audio playback: this runs inside the user's click.
      useTtsStore.getState().stop();
      listen(session);
    } catch {
      setError('Microphone access denied');
      setPhase('off');
    }
  }, [phase, listen]);

  /** Cut the assistant off mid-sentence (or skip waiting) and listen again. */
  const interrupt = useCallback(() => {
    if (phase === 'speaking') {
      // The TTS subscription resumes listening once playback is idle.
      useTtsStore.getState().stop();
    } else if (phase === 'listening') {
      // Treat a tap while listening as "I'm done talking".
      const rec = recorderRef.current;
      if (rec && rec.state === 'recording') {
        clearTimer();
        recorderRef.current = null;
        rec.stop();
      }
    }
  }, [phase]);

  useEffect(() => () => {
    sessionRef.current += 1;
    teardown();
  }, [teardown]);

  return {
    phase,
    level,
    error,
    active: phase !== 'off',
    start,
    stop,
    interrupt,
  };
}
