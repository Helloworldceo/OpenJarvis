import { X } from 'lucide-react';
import type { ConversationPhase } from '../../hooks/useConversationMode';

interface ConversationOrbProps {
  phase: ConversationPhase;
  /** Live microphone level, 0..1. */
  level: number;
  onTap: () => void;
  onEnd: () => void;
}

const LABELS: Record<Exclude<ConversationPhase, 'off'>, string> = {
  listening: 'Listening… just talk, I’ll answer when you pause',
  transcribing: 'Got it…',
  thinking: 'Thinking…',
  speaking: 'Speaking… tap the orb to interrupt',
};

/** Animated status orb for hands-free conversation mode. */
export function ConversationOrb({ phase, level, onTap, onEnd }: ConversationOrbProps) {
  if (phase === 'off') return null;

  const scale = phase === 'listening' ? 1 + level * 0.35 : 1;
  const tappable = phase === 'listening' || phase === 'speaking';

  return (
    <div
      className="flex items-center gap-4 mb-3 px-4 py-3 rounded-2xl"
      style={{
        background: 'var(--color-bg-secondary)',
        border: '1px solid var(--color-border)',
      }}
      role="status"
      aria-live="polite"
    >
      <button
        type="button"
        onClick={onTap}
        disabled={!tappable}
        aria-label={phase === 'speaking' ? 'Interrupt' : 'Done talking'}
        className={`conversation-orb conversation-orb--${phase} shrink-0`}
        style={{ transform: `scale(${scale})`, cursor: tappable ? 'pointer' : 'default' }}
      >
        <span className="conversation-orb__ring" />
      </button>
      <div className="flex-1 text-sm" style={{ color: 'var(--color-text-secondary)' }}>
        {LABELS[phase]}
      </div>
      <button
        type="button"
        onClick={onEnd}
        title="End conversation"
        className="p-2 rounded-xl transition-colors shrink-0 cursor-pointer"
        style={{ color: 'var(--color-text-tertiary)' }}
      >
        <X size={16} />
      </button>
    </div>
  );
}
