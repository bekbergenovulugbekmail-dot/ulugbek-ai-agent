"use client";

import type { ToolExecution } from "@/lib/api";
import { cx, formatDuration, formatRelative, humanize } from "@/lib/format";
import { permissionTone, toolTone } from "@/lib/status";

import { Badge } from "../ui/Badge";

/**
 * One tool invocation.
 *
 * Universal by design: a future GitHub, Railway, Telegram or Instagram adapter
 * renders through this same card, because the backend describes every tool with
 * the same fields. Inputs and outputs are summarised, never dumped — and they
 * arrive already redacted.
 */
export function ToolExecutionCard({
  execution,
  compact = false,
}: {
  execution: ToolExecution;
  compact?: boolean;
}) {
  const tone = toolTone(execution.status);
  const argCount = Object.keys(execution.arguments ?? {}).length;

  return (
    <article
      className={cx("panel px-4", compact ? "py-3" : "py-3.5")}
      data-testid="tool-execution"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span
              aria-hidden
              className={cx(
                "flex h-6 w-6 items-center justify-center rounded-md text-xs",
                tone === "ok" && "bg-ok-soft text-ok",
                tone === "danger" && "bg-danger-soft text-danger",
                tone === "warn" && "bg-warn-soft text-warn",
                tone === "neutral" && "bg-white/5 text-ink-faint",
              )}
            >
              {execution.status === "SUCCESS" ? "✓" : "✕"}
            </span>
            <h3 className="truncate text-sm font-medium text-ink">
              {humanize(execution.tool_name)}
            </h3>
          </div>
          <p className="mt-1 pl-8 text-xs text-ink-faint">
            {execution.status === "SUCCESS"
              ? `Completed${argCount ? ` · ${argCount} argument${argCount > 1 ? "s" : ""}` : ""}`
              : (execution.error ?? execution.status)}
          </p>
        </div>

        <div className="flex shrink-0 flex-col items-end gap-1.5">
          <Badge tone={tone}>{execution.status}</Badge>
          <span className="font-mono text-2xs text-ink-faint">
            {formatDuration(execution.duration_ms)}
          </span>
        </div>
      </div>

      {!compact && (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-line pt-2.5 text-2xs text-ink-faint">
          <Badge tone={permissionTone(execution.permission)}>
            {execution.permission}
          </Badge>
          {execution.verification_status && (
            <span
              className={cx(
                execution.verification_status === "FAILURE" && "text-danger",
              )}
            >
              Verification: {execution.verification_status}
            </span>
          )}
          <span className="ml-auto font-mono">
            {formatRelative(execution.created_at)}
          </span>
        </div>
      )}
    </article>
  );
}
