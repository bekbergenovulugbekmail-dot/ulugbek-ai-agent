"use client";

/**
 * Loading, empty and error states.
 *
 * One implementation each, so every screen fails and waits the same way — and
 * so an error never dumps a stack trace at the operator.
 */

import { ApiError } from "@/lib/api";
import { cx } from "@/lib/format";

import { Button } from "./Button";

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={cx(
        "relative overflow-hidden rounded-md bg-white/5",
        className,
      )}
    >
      <div className="absolute inset-0 -translate-x-full animate-shimmer bg-gradient-to-r from-transparent via-white/[0.06] to-transparent" />
    </div>
  );
}

export function LoadingState({
  rows = 3,
  label = "Loading",
}: {
  rows?: number;
  label?: string;
}) {
  return (
    <div className="space-y-2.5 p-5" role="status" aria-label={label}>
      {Array.from({ length: rows }).map((_, index) => (
        <Skeleton key={index} className="h-14 w-full" />
      ))}
      <span className="sr-only">{label}…</span>
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
  icon = "○",
}: {
  title: string;
  description?: string;
  action?: React.ReactNode;
  icon?: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      <div className="mb-1 flex h-9 w-9 items-center justify-center rounded-full border border-line text-ink-faint">
        <span aria-hidden>{icon}</span>
      </div>
      <p className="text-sm font-medium text-ink">{title}</p>
      {description && (
        <p className="max-w-sm text-xs leading-relaxed text-ink-faint">
          {description}
        </p>
      )}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

/**
 * A failure the operator can act on: what broke, why, and what to do — never
 * the raw exception.
 */
export function ErrorState({
  error,
  onRetry,
  compact = false,
}: {
  error: ApiError | Error;
  onRetry?: () => void;
  compact?: boolean;
}) {
  const apiError = error instanceof ApiError ? error : undefined;
  // "The backend", not "the agent": a configuration error can be the model
  // credential, the operator token or the runner, and only one of those is the
  // agent.
  const title = apiError?.isConfiguration
    ? "The backend is not configured"
    : apiError?.code === "network_error"
      ? "Cannot reach the backend"
      : "Something went wrong";

  // The hint says where to fix a thing, never which thing. Every
  // configuration error the backend raises already names its own variable and
  // what to do about it — `ANTHROPIC_WORKSPACE_ID`, `AUTH_TOKEN`, the LLM
  // client, the runner — so a fixed line recommending one of them contradicts
  // the message directly above it whenever the cause is another. That happened
  // with the workspace id: the message was exact, the hint pointed at the API
  // key, and the hint won the reader's attention.
  const hint = apiError?.isConfiguration
    ? "That is the backend's own message. The setting it refers to lives on the API service, not in this console."
    : apiError?.code === "network_error"
      ? "Check that the API is running and NEXT_PUBLIC_API_BASE_URL points at it."
      : undefined;

  return (
    <div
      role="alert"
      className={cx(
        "flex flex-col items-start gap-2 rounded-lg border border-danger/25 bg-danger-soft",
        compact ? "px-3 py-2.5" : "px-5 py-4",
      )}
    >
      <div className="flex items-center gap-2">
        <span aria-hidden className="text-danger">
          ✕
        </span>
        <p className="text-sm font-medium text-ink">{title}</p>
      </div>
      <p className="text-xs leading-relaxed text-ink-muted">{error.message}</p>
      {hint && <p className="text-xs text-ink-faint">{hint}</p>}
      {onRetry && (
        <Button size="sm" variant="secondary" onClick={onRetry} className="mt-1">
          Retry
        </Button>
      )}
    </div>
  );
}
