/**
 * End-of-utterance detection for hands-free conversation mode.
 *
 * The detector is fed one loudness sample at a time and decides when the user
 * has finished speaking: speech must first rise above the threshold for a
 * minimum duration (so a cough or a click does not count), and the turn ends
 * once the level has stayed below it for `silenceMs`. Kept free of Web Audio so
 * it can be unit tested with plain numbers.
 */

export type VadDecision = 'waiting' | 'speaking' | 'done' | 'idle-timeout';

export interface VadOptions {
  /** RMS level (0..1) above which a sample counts as speech. */
  threshold: number;
  /** Trailing silence that ends an utterance. */
  silenceMs: number;
  /** Speech needed before an utterance is considered started. */
  minSpeechMs: number;
  /** Hard cap on one utterance, so a noisy room cannot record forever. */
  maxUtteranceMs: number;
  /** Give up (and let the caller restart) after this long with no speech. */
  idleTimeoutMs: number;
}

export const DEFAULT_VAD_OPTIONS: VadOptions = {
  threshold: 0.02,
  silenceMs: 1200,
  minSpeechMs: 250,
  maxUtteranceMs: 60_000,
  idleTimeoutMs: 30_000,
};

export interface SilenceDetector {
  push: (level: number, nowMs: number) => VadDecision;
  /** True once enough speech has been heard to count as an utterance. */
  readonly heardSpeech: boolean;
}

export function createSilenceDetector(
  startMs: number,
  options: Partial<VadOptions> = {},
): SilenceDetector {
  const opts = { ...DEFAULT_VAD_OPTIONS, ...options };
  let speechStart: number | null = null;
  let heardSpeech = false;
  let lastLoud: number | null = null;

  return {
    get heardSpeech() {
      return heardSpeech;
    },
    push(level: number, nowMs: number): VadDecision {
      const loud = level >= opts.threshold;
      if (loud) {
        lastLoud = nowMs;
        if (speechStart === null) speechStart = nowMs;
        if (nowMs - speechStart >= opts.minSpeechMs) heardSpeech = true;
      } else if (!heardSpeech && lastLoud !== null && nowMs - lastLoud >= opts.silenceMs) {
        // A short blip that never reached minSpeechMs: forget it.
        speechStart = null;
        lastLoud = null;
      }

      if (!heardSpeech) {
        return nowMs - startMs >= opts.idleTimeoutMs ? 'idle-timeout' : 'waiting';
      }
      if (nowMs - (speechStart ?? nowMs) >= opts.maxUtteranceMs) return 'done';
      if (!loud && lastLoud !== null && nowMs - lastLoud >= opts.silenceMs) return 'done';
      return 'speaking';
    },
  };
}

/** Root-mean-square loudness (0..1) of an AnalyserNode byte time-domain frame. */
export function rmsFromTimeDomain(frame: Uint8Array): number {
  if (frame.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < frame.length; i++) {
    const v = (frame[i] - 128) / 128;
    sum += v * v;
  }
  return Math.sqrt(sum / frame.length);
}
