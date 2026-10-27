/**
 * Spoken hand-off to Claude: "Claude, check my Downloads folder" sends the
 * rest of the sentence to the Claude Code agent instead of the local model.
 *
 * Speech-to-text often hears "Claude" as "cloud" or "clod", so those count
 * too, but only as the first word (optionally after "hey"/"ok").
 */
const HANDOFF = /^\s*(?:(?:hey|hi|ok|okay)[\s,]+)?(?:claude|cloud|clod)\b[\s,.:;!?-]*(.*)$/is;

/** Return the task to hand to Claude, or null when the turn is not addressed to Claude. */
export function parseClaudeHandoff(text: string): string | null {
  const match = HANDOFF.exec(text);
  if (!match) return null;
  const task = match[1].trim();
  return task.length > 0 ? task : null;
}
