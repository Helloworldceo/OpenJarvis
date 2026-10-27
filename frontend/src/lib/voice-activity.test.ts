import { describe, expect, it } from 'vitest';
import { createSilenceDetector, rmsFromTimeDomain } from './voice-activity';

const opts = { threshold: 0.1, silenceMs: 1000, minSpeechMs: 200, maxUtteranceMs: 10_000, idleTimeoutMs: 5000 };

function feed(levels: Array<[number, number]>) {
  const vad = createSilenceDetector(0, opts);
  return levels.map(([t, level]) => vad.push(level, t));
}

describe('createSilenceDetector', () => {
  it('waits while the room is quiet', () => {
    expect(feed([[0, 0], [500, 0.01], [1000, 0]])).toEqual(['waiting', 'waiting', 'waiting']);
  });

  it('ends the utterance after trailing silence', () => {
    const out = feed([[0, 0.5], [300, 0.5], [600, 0.0], [1200, 0.0], [1400, 0.0]]);
    expect(out).toEqual(['waiting', 'speaking', 'speaking', 'speaking', 'done']);
  });

  it('ignores a short blip below minSpeechMs', () => {
    const out = feed([[0, 0.5], [100, 0.0], [1200, 0.0], [2000, 0.0]]);
    expect(out.every((d) => d === 'waiting')).toBe(true);
  });

  it('keeps listening through short pauses mid-sentence', () => {
    const out = feed([[0, 0.5], [300, 0.5], [700, 0.0], [1100, 0.5], [1800, 0.0]]);
    expect(out[out.length - 1]).toBe('speaking');
  });

  it('times out when nobody speaks', () => {
    expect(feed([[4000, 0], [5000, 0]])).toEqual(['waiting', 'idle-timeout']);
  });

  it('caps very long utterances', () => {
    const out = feed([[0, 0.5], [300, 0.5], [10_000, 0.5]]);
    expect(out[out.length - 1]).toBe('done');
  });
});

describe('rmsFromTimeDomain', () => {
  it('is zero for silence (128 = center)', () => {
    expect(rmsFromTimeDomain(new Uint8Array([128, 128, 128]))).toBe(0);
  });

  it('is near one for a full-scale square wave', () => {
    expect(rmsFromTimeDomain(new Uint8Array([0, 255, 0, 255]))).toBeGreaterThan(0.99);
  });
});
