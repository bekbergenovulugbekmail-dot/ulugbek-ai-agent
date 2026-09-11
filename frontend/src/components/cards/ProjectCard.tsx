"use client";

import Link from "next/link";

import type { Project } from "@/lib/api";
import { formatRelative, truncate } from "@/lib/format";
import { projectTone } from "@/lib/status";

import { Badge } from "../ui/Badge";

export function ProjectCard({
  project,
  taskCount,
}: {
  project: Project;
  taskCount?: number;
}) {
  const integrations = Object.keys(project.integrations ?? {});

  return (
    <Link
      href={`/projects/${project.id}`}
      className="panel panel-hover flex flex-col gap-3 px-4 py-4"
      data-testid="project-card"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate text-sm font-semibold text-ink">
            {project.name}
          </h3>
          <p className="mt-0.5 font-mono text-2xs text-ink-faint">
            {project.slug}
          </p>
        </div>
        <Badge tone={projectTone(project.status)} dot>
          {project.status}
        </Badge>
      </div>

      {project.description && (
        <p className="text-xs leading-relaxed text-ink-muted">
          {truncate(project.description, 150)}
        </p>
      )}

      <div className="mt-auto flex flex-wrap items-center gap-x-3 gap-y-1.5 border-t border-line pt-3 text-2xs text-ink-faint">
        {project.repository && (
          <span className="truncate font-mono">{project.repository}</span>
        )}
        {project.environment && <span>{project.environment}</span>}
        {taskCount !== undefined && <span>{taskCount} tasks</span>}
        {integrations.length > 0 ? (
          <span className="ml-auto flex gap-1">
            {integrations.slice(0, 3).map((name) => (
              <span
                key={name}
                className="rounded bg-white/5 px-1.5 py-0.5 text-ink-muted"
              >
                {name}
              </span>
            ))}
          </span>
        ) : (
          <span className="ml-auto">{formatRelative(project.updated_at)}</span>
        )}
      </div>
    </Link>
  );
}
