"use client";

import { useState } from "react";

import { ApiError, approvalApi, type Approval } from "@/lib/api";
import { cx, formatRelative, humanize } from "@/lib/format";
import { approvalTone, permissionTone } from "@/lib/status";

import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { ErrorState } from "../ui/States";

/**
 * A gated action awaiting a decision.
 *
 * Arguments were redacted server-side before the row was written, so what is
 * displayed is already safe; the card still renders them as inert text.
 */
export function ApprovalCard({
  approval,
  onDecided,
}: {
  approval: Approval;
  onDecided?: (approvalId: string, approved: boolean) => void;
}) {
  const [pending, setPending] = useState<"approve" | "reject" | null>(null);
  const [error, setError] = useState<ApiError | undefined>();

  const decide = async (approve: boolean) => {
    setPending(approve ? "approve" : "reject");
    setError(undefined);
    try {
      if (approve) await approvalApi.approve(approval.id);
      else await approvalApi.reject(approval.id);
      onDecided?.(approval.id, approve);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught
          : new ApiError("The decision could not be recorded."),
      );
    } finally {
      setPending(null);
    }
  };

  const decidable = approval.status === "PENDING";
  const args = Object.entries(approval.tool_arguments ?? {});

  return (
    <article
      className={cx(
        "panel overflow-hidden",
        decidable && "border-warn/25",
      )}
      data-testid="approval-card"
    >
      {decidable && (
        <div className="flex items-center gap-2 border-b border-warn/20 bg-warn-soft px-5 py-2">
          <span aria-hidden className="text-warn">
            ⏳
          </span>
          <p className="text-2xs font-semibold uppercase tracking-wider text-warn">
            Waiting for your approval
          </p>
        </div>
      )}

      <div className="space-y-3 px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-ink">
              {humanize(approval.tool_name)}
            </h3>
            {approval.goal && (
              <p className="mt-0.5 text-xs text-ink-faint">{approval.goal}</p>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            <Badge tone={permissionTone(approval.permission)}>
              {approval.permission}
            </Badge>
            {!decidable && (
              <Badge tone={approvalTone(approval.status)}>
                {approval.status}
              </Badge>
            )}
          </div>
        </div>

        <p className="text-sm leading-relaxed text-ink-muted">
          {approval.reason}
        </p>

        {args.length > 0 && (
          <dl className="grid gap-1.5 rounded-lg border border-line bg-elevated px-3 py-2.5">
            {args.map(([key, value]) => (
              <div key={key} className="flex gap-2 text-xs">
                <dt className="shrink-0 font-mono text-ink-faint">{key}</dt>
                <dd className="min-w-0 flex-1 truncate font-mono text-ink-muted">
                  {String(value)}
                </dd>
              </div>
            ))}
          </dl>
        )}

        {error && <ErrorState error={error} compact />}

        <div className="flex flex-wrap items-center gap-2 pt-0.5">
          <span className="mr-auto font-mono text-2xs text-ink-faint">
            {formatRelative(approval.created_at)}
          </span>
          {decidable ? (
            <>
              <Button
                variant="danger"
                size="sm"
                loading={pending === "reject"}
                disabled={pending !== null}
                onClick={() => decide(false)}
              >
                Reject
              </Button>
              <Button
                variant="primary"
                size="sm"
                loading={pending === "approve"}
                disabled={pending !== null}
                onClick={() => decide(true)}
              >
                Approve
              </Button>
            </>
          ) : (
            <span className="text-xs text-ink-faint">
              {approval.decided_by
                ? `Decided by ${approval.decided_by}`
                : "Decided"}
            </span>
          )}
        </div>
      </div>
    </article>
  );
}
