"use client";

import { useState } from "react";

import { ProjectCard } from "@/components/cards/ProjectCard";
import { PageHeader } from "@/components/layout/PageHeader";
import { FilterTabs } from "@/components/ui/Field";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/States";
import { projectApi, type ProjectStatus } from "@/lib/api";
import { useResource } from "@/lib/hooks/useResource";

const FILTERS = ["ALL", "ACTIVE", "PAUSED", "ARCHIVED"] as const;
type Filter = (typeof FILTERS)[number];

export default function ProjectsPage() {
  const [filter, setFilter] = useState<Filter>("ALL");

  const projects = useResource(
    (signal) =>
      projectApi.list(
        {
          status: filter === "ALL" ? undefined : (filter as ProjectStatus),
          limit: 60,
        },
        signal,
      ),
    [filter],
  );

  return (
    <div>
      <PageHeader
        title="Projects"
        description="Everything the agent can act on. Integrations attach per project."
        action={
          <FilterTabs options={FILTERS} value={filter} onChange={setFilter} />
        }
      />

      {projects.loading ? (
        <LoadingState rows={3} label="Loading projects" />
      ) : projects.error ? (
        <ErrorState error={projects.error} onRetry={projects.reload} />
      ) : (projects.data?.length ?? 0) === 0 ? (
        <div className="panel">
          <EmptyState
            icon="▤"
            title="No projects yet"
            description="Create a project through the API to give the agent something to route work to."
          />
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {projects.data?.map((project) => (
            <ProjectCard key={project.id} project={project} />
          ))}
        </div>
      )}
    </div>
  );
}
