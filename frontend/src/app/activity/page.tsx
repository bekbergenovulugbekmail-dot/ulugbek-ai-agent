"use client";

import { useMemo, useState } from "react";

import { ActivityTimeline } from "@/components/agent/ActivityTimeline";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Select } from "@/components/ui/Field";
import { ErrorState, LoadingState } from "@/components/ui/States";
import { eventApi, projectApi } from "@/lib/api";
import { useResource } from "@/lib/hooks/useResource";

const PAGE_SIZE = 40;

/** Backend step types, grouped as the operator thinks about them. */
const KIND_FILTERS = {
  "": "All activity",
  "tool_call,tool_result": "Tools",
  "plan,replan": "Planning",
  "approval,permission": "Approvals",
  "verification,verification_started": "Verification",
  "error,final_result": "Outcomes",
} as const;

export default function ActivityPage() {
  const [kind, setKind] = useState<keyof typeof KIND_FILTERS>("");
  const [projectId, setProjectId] = useState("");
  const [limit, setLimit] = useState(PAGE_SIZE);

  const projects = useResource((signal) => projectApi.list({ limit: 50 }, signal), []);

  const types = useMemo(() => (kind ? kind.split(",") : undefined), [kind]);

  const events = useResource(
    (signal) =>
      eventApi.list(
        { type: types, project_id: projectId || undefined, limit },
        signal,
      ),
    [kind, projectId, limit],
    { pollMs: 8_000 },
  );

  return (
    <div>
      <PageHeader
        title="Activity"
        description="A single timeline of everything the agent has done."
      />

      <div className="mb-4 flex flex-wrap gap-2">
        <label htmlFor="activity-kind" className="sr-only">
          Activity type
        </label>
        <Select
          id="activity-kind"
          value={kind}
          onChange={(event) => {
            setKind(event.target.value as keyof typeof KIND_FILTERS);
            setLimit(PAGE_SIZE);
          }}
        >
          {Object.entries(KIND_FILTERS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </Select>

        <label htmlFor="activity-project" className="sr-only">
          Project
        </label>
        <Select
          id="activity-project"
          value={projectId}
          onChange={(event) => {
            setProjectId(event.target.value);
            setLimit(PAGE_SIZE);
          }}
        >
          <option value="">All projects</option>
          {projects.data?.map((project) => (
            <option key={project.id} value={project.id}>
              {project.name}
            </option>
          ))}
        </Select>
      </div>

      <Card>
        {events.loading ? (
          <LoadingState rows={6} label="Loading activity" />
        ) : events.error ? (
          <div className="p-5">
            <ErrorState error={events.error} onRetry={events.reload} />
          </div>
        ) : (
          <div className="p-3">
            <ActivityTimeline
              events={events.data?.events ?? []}
              emptyLabel="No activity recorded"
            />
          </div>
        )}
      </Card>

      {events.data?.has_more && (
        <div className="pt-3 text-center">
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
  );
}
