"use client";

import Link from "next/link";

import type { Task } from "@/lib/api";
import { formatRelative, truncate } from "@/lib/format";
import { taskTone } from "@/lib/status";

import { ProgressIndicator } from "../agent/ProgressIndicator";
import { Badge } from "../ui/Badge";

export function TaskCard({
  task,
  projectName,
}: {
  task: Task;
  projectName?: string;
}) {
  const steps = Array.isArray(task.steps) ? task.steps.length : 0;
  const active = task.status === "RUNNING" || task.status === "PLANNING";

  return (
    <Link
      href={`/tasks/${task.id}`}
      className="panel panel-hover block px-4 py-3.5"
      data-testid="task-card"
    >
      <div className="flex items-start justify-between gap-3">
        <p className="min-w-0 text-sm font-medium leading-snug text-ink">
          {truncate(task.goal, 140)}
        </p>
        <Badge tone={taskTone(task.status)} dot pulse={active}>
          {task.status.replace(/_/g, " ")}
        </Badge>
      </div>

      {steps > 0 && (
        <div className="mt-3">
          <ProgressIndicator
            current={task.current_step}
            total={steps}
            label="Plan steps"
            tone={
              task.status === "FAILED"
                ? "danger"
                : task.status === "COMPLETED"
                  ? "ok"
                  : "accent"
            }
          />
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-ink-faint">
        {projectName && (
          <span className="rounded bg-white/5 px-1.5 py-0.5">{projectName}</span>
        )}
        <span className="font-mono">{formatRelative(task.created_at)}</span>
        {task.error && (
          <span className="truncate text-danger">{truncate(task.error, 80)}</span>
        )}
      </div>
    </Link>
  );
}
