"use client";

import type { VerificationStatus } from "@/lib/api";
import { cx, formatTime } from "@/lib/format";

import { Badge } from "../ui/Badge";

export type ChatRole = "user" | "agent" | "system";

export interface ChatEntry {
  id: string;
  role: ChatRole;
  text: string;
  timestamp: string;
  verification?: { status: VerificationStatus; reason: string } | null;
  failed?: boolean;
}

/**
 * One turn of the conversation.
 *
 * An agent answer carries its verification verdict, because "the agent said so"
 * and "the agent proved it" are different claims and the operator should see
 * which one they are getting.
 */
export function ChatMessage({ entry }: { entry: ChatEntry }) {
  const isUser = entry.role === "user";

  if (entry.role === "system") {
    return (
      <div className="px-1 py-2 text-center">
        <span className="text-xs text-ink-faint">{entry.text}</span>
      </div>
    );
  }

  return (
    <div
      className={cx("flex gap-3", isUser ? "justify-end" : "justify-start")}
      data-testid={`chat-${entry.role}`}
    >
      {!isUser && (
        <span
          aria-hidden
          className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-accent-line bg-accent-soft text-2xs font-bold text-accent"
        >
          U
        </span>
      )}
      <div
        className={cx(
          "max-w-[min(46rem,85%)] rounded-xl border px-4 py-3",
          isUser
            ? "border-line-strong bg-elevated"
            : entry.failed
              ? "border-danger/25 bg-danger-soft"
              : "border-line bg-surface",
        )}
      >
        <div className="mb-1 flex items-center gap-2">
          <span className="text-2xs font-medium uppercase tracking-wider text-ink-faint">
            {isUser ? "You" : "Ulugbek AI"}
          </span>
          <time
            className="font-mono text-2xs text-ink-faint"
            dateTime={entry.timestamp}
          >
            {formatTime(entry.timestamp)}
          </time>
        </div>
        <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-ink">
          {entry.text}
        </p>
        {entry.verification && (
          <div className="mt-2.5 flex flex-wrap items-center gap-2 border-t border-line pt-2">
            <Badge
              tone={
                entry.verification.status === "SUCCESS"
                  ? "ok"
                  : entry.verification.status === "FAILURE"
                    ? "danger"
                    : "neutral"
              }
            >
              Verified: {entry.verification.status}
            </Badge>
            <span className="text-xs text-ink-faint">
              {entry.verification.reason}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
