"use client";

import Link from "next/link";

import { ActivityTimeline } from "@/components/agent/ActivityTimeline";
import { AgentStatus } from "@/components/agent/AgentStatus";
import { CommandInput } from "@/components/agent/CommandInput";
import { ApprovalCard } from "@/components/cards/ApprovalCard";
import { StatTile } from "@/components/cards/StatTile";
import { Card, CardHeader } from "@/components/ui/Card";
import { ErrorBoundary } from "@/components/ui/ErrorBoundary";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  Skeleton,
} from "@/components/ui/States";
import { HealthList } from "@/components/system/HealthIndicator";
import { agentApi, approvalApi, eventApi, healthApi } from "@/lib/api";
import { greeting } from "@/lib/format";
import { useResource } from "@/lib/hooks/useResource";
import { useRouter } from "next/navigation";
import { useState } from "react";

export default function DashboardPage() {
  const router = useRouter();
  const [dispatching, setDispatching] = useState(false);

  const overview = useResource((signal) => healthApi.overview(signal), [], {
    pollMs: 6_000,
  });
  const activity = useResource(
    (signal) => eventApi.list({ limit: 12 }, signal),
    [],
    { pollMs: 6_000 },
  );
  const approvals = useResource(
    (signal) => approvalApi.list({ status: "PENDING", limit: 3 }, signal),
    [],
    { pollMs: 6_000 },
  );

  /** Send from the dashboard, then follow the run on the Agent page. */
  const dispatch = async (message: string) => {
    setDispatching(true);
    try {
      const response = await agentApi.start({ message });
      router.push(`/agent?run=${response.run_id}`);
    } catch {
      router.push(`/agent?draft=${encodeURIComponent(message)}`);
    } finally {
      setDispatching(false);
    }
  };

  const counters = overview.data?.counters;
  const agent = overview.data?.agent;

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-ink">
            {greeting()}, Ulugbek
          </h1>
          <p className="mt-1 text-sm text-ink-faint">Your AI workspace</p>
        </div>
        <AgentStatus state={agent} detail={false} />
      </header>

      <CommandInput
        onSubmit={dispatch}
        busy={dispatching}
        placeholder="Ask Ulugbek AI anything…"
      />

      {overview.error && (
        <ErrorState error={overview.error} onRetry={overview.reload} />
      )}

      <section
        aria-label="Overview"
        className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5"
      >
        {overview.loading && !counters ? (
          Array.from({ length: 5 }).map((_, index) => (
            <Card key={index} className="h-[88px]">
              <Skeleton className="h-full w-full" />
            </Card>
          ))
        ) : (
          <>
            <StatTile
              label="Active tasks"
              value={counters?.active_tasks ?? 0}
              href="/tasks"
              tone="accent"
              emphasise={(counters?.active_tasks ?? 0) > 0}
            />
            <StatTile
              label="Projects"
              value={counters?.active_projects ?? 0}
              hint={`${counters?.projects ?? 0} total`}
              href="/projects"
            />
            <StatTile
              label="Approvals"
              value={counters?.pending_approvals ?? 0}
              href="/approvals"
              tone="warn"
              emphasise={(counters?.pending_approvals ?? 0) > 0}
            />
            <StatTile
              label="Memories"
              value={counters?.memories ?? 0}
              href="/memory"
            />
            <StatTile
              label="Tool runs"
              value={counters?.tool_executions ?? 0}
              href="/tools"
            />
          </>
        )}
      </section>

      {(approvals.data?.length ?? 0) > 0 && (
        <section aria-label="Approvals needed" className="space-y-3">
          {approvals.data?.map((approval) => (
            <ApprovalCard
              key={approval.id}
              approval={approval}
              onDecided={() => {
                approvals.reload();
                overview.reload();
              }}
            />
          ))}
        </section>
      )}

      <div className="grid gap-5 lg:grid-cols-3">
        <ErrorBoundary label="Agent activity">
          <Card className="min-w-0 lg:col-span-2">
            <CardHeader
              title="Agent activity"
              subtitle="Everything the agent has done, most recent first"
              action={
                <Link
                  href="/activity"
                  className="text-xs text-ink-faint hover:text-ink"
                >
                  View all →
                </Link>
              }
            />
            {activity.loading ? (
              <LoadingState rows={4} />
            ) : activity.error ? (
              <div className="p-5">
                <ErrorState error={activity.error} onRetry={activity.reload} />
              </div>
            ) : (
              <div className="p-3">
                <ActivityTimeline
                  events={activity.data?.events ?? []}
                  dense
                  emptyLabel="No activity yet"
                />
              </div>
            )}
          </Card>
        </ErrorBoundary>

        <div className="min-w-0 space-y-5">
          <Card>
            <CardHeader title="System health" />
            <div className="px-5 py-4">
              {overview.data ? (
                <HealthList components={overview.data.components} />
              ) : (
                <LoadingState rows={2} label="Checking system" />
              )}
            </div>
          </Card>

          <Card>
            <CardHeader title="Current task" />
            <div className="px-5 py-4">
              {agent?.goal ? (
                <div className="space-y-2">
                  <p className="text-sm leading-relaxed text-ink">
                    {agent.goal}
                  </p>
                  <AgentStatus state={agent} />
                  {agent.run_id && (
                    <Link
                      href={`/agent?run=${agent.run_id}`}
                      className="inline-block text-xs text-accent hover:underline"
                    >
                      Open in Agent →
                    </Link>
                  )}
                </div>
              ) : (
                <EmptyState
                  title="Nothing running"
                  description="Send a command above to put the agent to work."
                />
              )}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
