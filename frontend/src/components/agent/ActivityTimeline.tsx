"use client";

import type { AgentEvent } from "@/lib/api";
import { cx, formatDuration, formatTime } from "@/lib/format";
import { TONE_DOT, eventGlyph, eventTone } from "@/lib/status";

import { EmptyState } from "../ui/States";

/**
 * The run timeline.
 *
 * Every line comes from the backend's redacted audit trail, so nothing here can
 * leak a credential — and nothing here is the model's private reasoning, only
 * what it actually did.
 */
export function ActivityTimeline({
  events,
  live = false,
  dense = false,
  emptyLabel = "No activity yet",
}: {
  events: AgentEvent[];
  live?: boolean;
  dense?: boolean;
  emptyLabel?: string;
}) {
  if (events.length === 0) {
    return (
      <EmptyState
        title={emptyLabel}
        description="Activity appears here as soon as the agent starts working."
      />
    );
  }

  return (
    <ol className={cx("relative", dense ? "space-y-0.5" : "space-y-1")}>
      {events.map((event, index) => {
        const tone = eventTone(event.status);
        const last = index === events.length - 1;
        const running = event.status === "running" && live && last;
        const duration = event.metadata?.duration_ms as number | undefined;

        return (
          <li
            key={`${event.run_id}-${event.sequence}`}
            className="animate-fade-up"
            data-testid="timeline-event"
            data-event-type={event.type}
          >
            <div
              className={cx(
                "group flex items-start gap-3 rounded-lg px-2.5 transition-colors hover:bg-white/[0.03]",
                dense ? "py-1.5" : "py-2",
              )}
            >
              <div className="relative flex flex-col items-center self-stretch">
                <span
                  aria-hidden
                  className={cx(
                    "mt-1 flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[9px]",
                    tone === "ok" && "bg-ok-soft text-ok",
                    tone === "danger" && "bg-danger-soft text-danger",
                    tone === "warn" && "bg-warn-soft text-warn",
                    tone === "accent" && "bg-accent-soft text-accent",
                    tone === "neutral" && "bg-white/5 text-ink-faint",
                    tone === "info" && "bg-info-soft text-info",
                    running && "animate-pulse-soft",
                  )}
                >
                  {eventGlyph(event.type)}
                </span>
                {!last && (
                  <span
                    aria-hidden
                    className="mt-0.5 w-px flex-1 bg-line"
                  />
                )}
              </div>

              <div className="min-w-0 flex-1 pb-0.5">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <p className="text-sm text-ink">{event.safe_message}</p>
                  {event.subject && (
                    <code className="rounded bg-white/5 px-1 py-0.5 font-mono text-2xs text-ink-muted">
                      {event.subject}
                    </code>
                  )}
                </div>
                {typeof event.metadata?.error === "string" && (
                  <p className="mt-0.5 text-xs text-danger">
                    {event.metadata.error as string}
                  </p>
                )}
              </div>

              <div className="flex shrink-0 items-center gap-2 pt-0.5">
                {duration !== undefined && (
                  <span className="font-mono text-2xs text-ink-faint">
                    {formatDuration(duration)}
                  </span>
                )}
                <time
                  className="font-mono text-2xs text-ink-faint opacity-0 transition-opacity group-hover:opacity-100"
                  dateTime={event.timestamp}
                >
                  {formatTime(event.timestamp)}
                </time>
                <span
                  aria-hidden
                  className={cx("h-1 w-1 rounded-full", TONE_DOT[tone])}
                />
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
