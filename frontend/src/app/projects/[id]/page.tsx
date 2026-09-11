"use client";

import Link from "next/link";
import { use } from "react";

import { ActivityTimeline } from "@/components/agent/ActivityTimeline";
import { TaskCard } from "@/components/cards/TaskCard";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/States";
import { projectApi } from "@/lib/api";
import { formatRelative, humanize, truncate } from "@/lib/format";
import { useResource } from "@/lib/hooks/useResource";
import { projectTone } from "@/lib/status";

export default function ProjectDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  // One request instead of five: the backend composes the detail view.
  const overview = useResource((signal) => projectApi.overview(id, signal), [id], {
    pollMs: 10_000,
  });

  if (overview.loading) return <LoadingState rows={5} label="Loading project" />;
  if (overview.error)
    return <ErrorState error={overview.error} onRetry={overview.reload} />;
  if (!overview.data) return null;

  const { project, tasks, memories, activity, integrations } = overview.data;

  return (
    <div className="space-y-5">
      <Link
        href="/projects"
        className="text-xs text-ink-faint hover:text-ink"
      >
        ← Projects
      </Link>

      <PageHeader
        title={project.name}
        description={project.description ?? undefined}
        action={<Badge tone={projectTone(project.status)} dot>{project.status}</Badge>}
      />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Card className="px-4 py-3">
          <p className="label-caps">Repository</p>
          <p className="mt-1 truncate font-mono text-sm text-ink">
            {project.repository ?? "—"}
          </p>
        </Card>
        <Card className="px-4 py-3">
          <p className="label-caps">Environment</p>
          <p className="mt-1 text-sm text-ink">{project.environment ?? "—"}</p>
        </Card>
        <Card className="px-4 py-3">
          <p className="label-caps">Integrations</p>
          <div className="mt-1.5 flex flex-wrap gap-1">
            {integrations.length > 0 ? (
              integrations.map((name) => (
                <Badge key={name} tone="info">
                  {name}
                </Badge>
              ))
            ) : (
              <span className="text-sm text-ink-faint">None yet</span>
            )}
          </div>
        </Card>
        <Card className="px-4 py-3">
          <p className="label-caps">Updated</p>
          <p className="mt-1 text-sm text-ink">
            {formatRelative(project.updated_at)}
          </p>
        </Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader
            title="Current tasks"
            subtitle={`${tasks.length} recent`}
            action={
              <Link
                href={`/tasks?project=${project.id}`}
                className="text-xs text-ink-faint hover:text-ink"
              >
                All →
              </Link>
            }
          />
          <div className="space-y-2 p-3">
            {tasks.length === 0 ? (
              <EmptyState title="No tasks yet" />
            ) : (
              tasks.map((task) => <TaskCard key={task.id} task={task} />)
            )}
          </div>
        </Card>

        <Card>
          <CardHeader title="Recent activity" />
          <div className="max-h-[24rem] overflow-y-auto p-3">
            <ActivityTimeline events={activity} dense />
          </div>
        </Card>
      </div>

      <Card>
        <CardHeader
          title="Project memory"
          subtitle="What the agent knows about this project"
          action={
            <Link
              href={`/memory?project=${project.id}`}
              className="text-xs text-ink-faint hover:text-ink"
            >
              All →
            </Link>
          }
        />
        <div className="divide-y divide-line">
          {memories.length === 0 ? (
            <EmptyState title="No memories yet" />
          ) : (
            memories.map((memory) => (
              <div key={memory.id} className="px-5 py-3">
                <div className="mb-1 flex items-center gap-2">
                  <Badge tone="neutral">{humanize(memory.type)}</Badge>
                  <span className="font-mono text-2xs text-ink-faint">
                    {formatRelative(memory.created_at)}
                  </span>
                </div>
                <p className="text-sm leading-relaxed text-ink-muted">
                  {truncate(memory.content, 240)}
                </p>
              </div>
            ))
          )}
        </div>
      </Card>
    </div>
  );
}
