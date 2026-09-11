"use client";

import Link from "next/link";
import { use } from "react";

import { ActivityTimeline } from "@/components/agent/ActivityTimeline";
import { ProgressIndicator } from "@/components/agent/ProgressIndicator";
import { ToolExecutionCard } from "@/components/cards/ToolExecutionCard";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/States";
import { eventApi, taskApi, toolApi } from "@/lib/api";
import { formatDateTime, humanize } from "@/lib/format";
import { useResource } from "@/lib/hooks/useResource";
import { taskTone } from "@/lib/status";

interface PlanStep {
  index: number;
  description: string;
  tool?: string | null;
  expected_outcome?: string | null;
  status: string;
}

export default function TaskDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);

  const task = useResource((signal) => taskApi.get(id, signal), [id], {
    pollMs: 6_000,
  });
  const events = useResource(
    (signal) => eventApi.list({ task_id: id, limit: 100 }, signal),
    [id],
    { pollMs: 6_000 },
  );
  const executions = useResource(
    (signal) => toolApi.executions({ task_id: id, limit: 25 }, signal),
    [id],
  );

  if (task.loading) return <LoadingState rows={5} label="Loading task" />;
  if (task.error) return <ErrorState error={task.error} onRetry={task.reload} />;
  if (!task.data) return null;

  const steps = (task.data.steps ?? []) as unknown as PlanStep[];
  const active = ["RUNNING", "PLANNING", "WAITING_APPROVAL"].includes(
    task.data.status,
  );

  return (
    <div className="space-y-5">
      <Link href="/tasks" className="text-xs text-ink-faint hover:text-ink">
        ← Tasks
      </Link>

      <PageHeader
        title={task.data.goal}
        description={`Created ${formatDateTime(task.data.created_at)}`}
        action={
          <Badge tone={taskTone(task.data.status)} dot pulse={active}>
            {task.data.status.replace(/_/g, " ")}
          </Badge>
        }
      />

      {task.data.error && (
        <div className="rounded-lg border border-danger/25 bg-danger-soft px-5 py-4">
          <p className="mb-1 text-sm font-medium text-ink">Task failed</p>
          <p className="text-xs leading-relaxed text-ink-muted">
            {task.data.error}
          </p>
        </div>
      )}

      {task.data.result && (
        <Card>
          <CardHeader title="Result" />
          <p className="whitespace-pre-wrap px-5 py-4 text-sm leading-relaxed text-ink">
            {task.data.result}
          </p>
        </Card>
      )}

      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader title="Plan" subtitle={`${steps.length} steps`} />
          <div className="px-5 py-4">
            {steps.length === 0 ? (
              <EmptyState title="No plan recorded" />
            ) : (
              <>
                <ProgressIndicator
                  current={task.data.current_step}
                  total={steps.length}
                  tone={
                    task.data.status === "FAILED"
                      ? "danger"
                      : task.data.status === "COMPLETED"
                        ? "ok"
                        : "accent"
                  }
                />
                <ol className="mt-4 space-y-2.5">
                  {steps.map((step) => (
                    <li key={step.index} className="flex gap-3">
                      <span className="mt-0.5 font-mono text-2xs text-ink-faint">
                        {String(step.index + 1).padStart(2, "0")}
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="text-sm text-ink">{step.description}</p>
                        {step.expected_outcome && (
                          <p className="mt-0.5 text-xs text-ink-faint">
                            Expect: {step.expected_outcome}
                          </p>
                        )}
                      </div>
                      <Badge
                        tone={
                          step.status === "DONE"
                            ? "ok"
                            : step.status === "FAILED"
                              ? "danger"
                              : "neutral"
                        }
                      >
                        {humanize(step.status)}
                      </Badge>
                    </li>
                  ))}
                </ol>
              </>
            )}
          </div>
        </Card>

        <Card>
          <CardHeader title="Execution timeline" />
          <div className="max-h-[28rem] overflow-y-auto p-3">
            <ActivityTimeline
              events={[...(events.data?.events ?? [])].reverse()}
              dense
            />
          </div>
        </Card>
      </div>

      {(executions.data?.length ?? 0) > 0 && (
        <Card>
          <CardHeader title="Tool executions" />
          <div className="space-y-2 p-3">
            {executions.data?.map((execution) => (
              <ToolExecutionCard key={execution.id} execution={execution} />
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
