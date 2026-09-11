"use client";

import { useState } from "react";

import { ApprovalCard } from "@/components/cards/ApprovalCard";
import { PageHeader } from "@/components/layout/PageHeader";
import { Card } from "@/components/ui/Card";
import { FilterTabs } from "@/components/ui/Field";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/States";
import { approvalApi, type ApprovalStatus } from "@/lib/api";
import { useResource } from "@/lib/hooks/useResource";

const FILTERS = ["PENDING", "APPROVED", "REJECTED", "EXPIRED"] as const;
type Filter = (typeof FILTERS)[number];

export default function ApprovalsPage() {
  const [filter, setFilter] = useState<Filter>("PENDING");

  const approvals = useResource(
    (signal) =>
      approvalApi.list({ status: filter as ApprovalStatus, limit: 40 }, signal),
    [filter],
    { pollMs: filter === "PENDING" ? 5_000 : undefined },
  );

  return (
    <div>
      <PageHeader
        title="Approval center"
        description="The agent pauses here before anything dangerous. Nothing runs without your decision."
        action={
          <FilterTabs options={FILTERS} value={filter} onChange={setFilter} />
        }
      />

      {approvals.loading ? (
        <LoadingState rows={2} label="Loading approvals" />
      ) : approvals.error ? (
        <ErrorState error={approvals.error} onRetry={approvals.reload} />
      ) : (approvals.data?.length ?? 0) === 0 ? (
        <Card>
          <EmptyState
            icon="✓"
            title={
              filter === "PENDING"
                ? "Nothing waiting on you"
                : `No ${filter.toLowerCase()} approvals`
            }
            description={
              filter === "PENDING"
                ? "When the agent reaches an EXECUTE, DELETE or CRITICAL action, it stops and asks here."
                : undefined
            }
          />
        </Card>
      ) : (
        <div className="space-y-3">
          {approvals.data?.map((approval) => (
            <ApprovalCard
              key={approval.id}
              approval={approval}
              onDecided={approvals.reload}
            />
          ))}
        </div>
      )}
    </div>
  );
}
