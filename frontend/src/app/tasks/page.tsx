"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";

import { TaskCard } from "@/components/cards/TaskCard";
import { PageHeader } from "@/components/layout/PageHeader";
import { FilterTabs } from "@/components/ui/Field";
import { Button } from "@/components/ui/Button";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/States";
import { healthApi, taskApi, type TaskStatus } from "@/lib/api";
import { useResource } from "@/lib/hooks/useResource";

const FILTERS = [
  "ALL",
  "PENDING",
  "RUNNING",
  "WAITING_APPROVAL",
  "COMPLETED",
  "FAILED",
  "CANCELLED",
] as const;
type Filter = (typeof FILTERS)[number];

const PAGE_SIZE = 20;

function TasksView() {
  const params = useSearchParams();
  const projectId = params.get("project") ?? undefined;

  const [filter, setFilter] = useState<Filter>("ALL");
  const [limit, setLimit] = useState(PAGE_SIZE);

  const tasks = useResource(
    (signal) =>
      taskApi.list(
        {
          status: filter === "ALL" ? undefined : (filter as TaskStatus),
          project_id: projectId,
          limit,
        },
        signal,
      ),
    [filter, projectId, limit],
    { pollMs: 8_000 },
  );

  const overview = useResource((signal) => healthApi.overview(signal), []);
  const counts = overview.data?.tasks_by_status;

  return (
    <div>
      <PageHeader
        title="Tasks"
        description="Every goal the agent has taken on."
        action={
          <FilterTabs
            options={FILTERS}
            value={filter}
            onChange={(next) => {
              setFilter(next);
              setLimit(PAGE_SIZE);
            }}
            counts={
              counts
                ? ({
                    ALL: overview.data?.counters.tasks,
                    ...counts,
                  } as Partial<Record<Filter, number>>)
                : undefined
            }
          />
        }
      />

      {tasks.loading ? (
        <LoadingState rows={4} label="Loading tasks" />
      ) : tasks.error ? (
        <ErrorState error={tasks.error} onRetry={tasks.reload} />
      ) : (tasks.data?.length ?? 0) === 0 ? (
        <div className="panel">
          <EmptyState
            icon="☰"
            title="No tasks here"
            description={
              filter === "ALL"
                ? "Send the agent a command and its task will appear here."
                : `No tasks with status ${filter.replace(/_/g, " ").toLowerCase()}.`
            }
          />
        </div>
      ) : (
        <div className="space-y-2.5">
          {tasks.data?.map((task) => <TaskCard key={task.id} task={task} />)}
          {(tasks.data?.length ?? 0) >= limit && (
            <div className="pt-2 text-center">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setLimit((value) => value + PAGE_SIZE)}
              >
                Load more
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function TasksPage() {
  return (
    <Suspense fallback={<LoadingState rows={4} label="Loading tasks" />}>
      <TasksView />
    </Suspense>
  );
}
