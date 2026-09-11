"use client";

import type { AgentStateSnapshot } from "@/lib/api";
import { cx, formatRelative } from "@/lib/format";
import { TONE_CLASSES, TONE_DOT, phaseTone } from "@/lib/status";

/**
 * The headline status.
 *
 * It always names the concrete phase — "Using a tool", "Verifying the result" —
 * so the operator is never left guessing what the agent is doing. The private
 * reasoning behind it is never surfaced; only phases and safe messages are.
 */
export function AgentStatus({
  state,
  detail = true,
}: {
  state: AgentStateSnapshot | undefined;
  detail?: boolean;
}) {
  const phase = state?.phase ?? "IDLE";
  const tone = phaseTone(phase);
  const busy = state?.busy ?? false;

  return (
    <div className="flex flex-wrap items-center gap-3">
      <span
        className={cx(
          "inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-semibold uppercase tracking-wider",
          TONE_CLASSES[tone],
        )}
        data-testid="agent-phase"
      >
        <span
          aria-hidden
          className={cx(
            "h-2 w-2 rounded-full",
            TONE_DOT[tone],
            busy && "animate-pulse-soft",
          )}
        />
        {phase.replace(/_/g, " ")}
      </span>

      <div className="min-w-0">
        <p className="truncate text-sm text-ink">{state?.label ?? "Idle"}</p>
        {detail && state?.detail && (
          <p className="truncate text-xs text-ink-faint">{state.detail}</p>
        )}
      </div>

      {state?.updated_at && (
        <span className="ml-auto shrink-0 font-mono text-2xs text-ink-faint">
          {formatRelative(state.updated_at)}
        </span>
      )}
    </div>
  );
}
