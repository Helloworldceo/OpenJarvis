import { describe, expect, it } from 'vitest';
import { parseClaudeHandoff } from './claude-handoff';

describe('parseClaudeHandoff', () => {
  it('extracts the task after "Claude,"', () => {
    expect(parseClaudeHandoff('Claude, list my Downloads folder.')).toBe('list my Downloads folder.');
  });

  it('accepts "hey Claude" and common mishearings', () => {
    expect(parseClaudeHandoff('Hey Claude what is in my notes')).toBe('what is in my notes');
    expect(parseClaudeHandoff('Cloud, summarize the README')).toBe('summarize the README');
    expect(parseClaudeHandoff('ok, clod: check the weather')).toBe('check the weather');
  });

  it('ignores turns not addressed to Claude', () => {
    expect(parseClaudeHandoff('What is the capital of France?')).toBeNull();
    expect(parseClaudeHandoff('I asked Claude yesterday')).toBeNull();
    expect(parseClaudeHandoff('Cloudy skies today?')).toBeNull();
  });

  it('needs an actual task', () => {
    expect(parseClaudeHandoff('Claude.')).toBeNull();
  });
});
