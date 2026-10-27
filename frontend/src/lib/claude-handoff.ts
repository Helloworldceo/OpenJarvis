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

const BRAINSTORM =
  /^\s*(?:(?:hey|ok|okay)[\s,]+)?(?:jarvis[\s,]+)?(?:(?:let'?s|please|can you|could you)\s+)?brainstorm\b(?:\s+with\s+(?:claude|cloud|clod)\b)?(?:\s+(?:about|on|for))?[\s,.:;!?-]*(.*)$/is;

/**
 * Return the topic of a spoken "brainstorm with Claude about …" request, or
 * null when the turn is not a brainstorm request.
 */
export function parseBrainstorm(text: string): string | null {
  const match = BRAINSTORM.exec(text);
  if (!match) return null;
  const topic = match[1].trim().replace(/[.!?]+$/, '');
  return topic.length > 0 ? topic : null;
}
