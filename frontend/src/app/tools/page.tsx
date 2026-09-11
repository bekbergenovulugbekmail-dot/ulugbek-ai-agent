"use client";

import { useState } from "react";

import { ToolExecutionCard } from "@/components/cards/ToolExecutionCard";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/States";
import { toolApi } from "@/lib/api";
import { humanize } from "@/lib/format";
import { useResource } from "@/lib/hooks/useResource";
import { serviceMeta } from "@/lib/services";
import { permissionTone } from "@/lib/status";

const PAGE_SIZE = 20;

export default function ToolsPage() {
  const [limit, setLimit] = useState(PAGE_SIZE);

  const registry = useResource((signal) => toolApi.list(signal), []);
  const executions = useResource(
    (signal) => toolApi.executions({ limit }, signal),
    [limit],
    { pollMs: 10_000 },
  );

  return (
    <div className="space-y-5">
      <PageHeader
        title="Tools"
        description="The agent's only way to affect the outside world. Each tool carries a permission level."
      />

      <Card>
        <CardHeader
          title="Registry"
          subtitle={`${registry.data?.count ?? 0} tools available`}
        />
        {registry.loading ? (
          <LoadingState rows={3} label="Loading tools" />
        ) : registry.error ? (
          <div className="p-5">
            <ErrorState error={registry.error} onRetry={registry.reload} />
          </div>
        ) : (
          <div className="divide-y divide-line">
            {registry.data?.tools.map((tool) => (
              <div
                key={tool.name}
                className="flex flex-wrap items-start gap-3 px-5 py-3.5"
                data-testid="tool-descriptor"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <h3 className="text-sm font-medium text-ink">
                      {humanize(tool.name)}
                    </h3>
                    <code className="font-mono text-2xs text-ink-faint">
                      {tool.name}
                    </code>
                    {tool.service && (
                      <span className="inline-flex items-center gap-1 rounded border border-line px-1.5 py-0.5 text-2xs text-ink-muted">
                        <span aria-hidden>{serviceMeta(tool.service).glyph}</span>
                        {serviceMeta(tool.service).label}
                      </span>
                    )}
                  </div>
                  <p className="mt-1 text-xs leading-relaxed text-ink-faint">
                    {tool.description}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {tool.timeout_seconds && (
                    <span className="font-mono text-2xs text-ink-faint">
                      {tool.timeout_seconds}s
                    </span>
                  )}
                  <Badge tone={permissionTone(tool.permission)}>
                    {tool.permission}
                  </Badge>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card>
        <CardHeader
          title="Recent executions"
          subtitle="Inputs and outputs are redacted before they are stored"
        />
        {executions.loading ? (
          <LoadingState rows={3} label="Loading executions" />
        ) : executions.error ? (
          <div className="p-5">
            <ErrorState error={executions.error} onRetry={executions.reload} />
          </div>
        ) : (executions.data?.length ?? 0) === 0 ? (
          <EmptyState
            icon="⚙"
            title="No tool has run yet"
            description="Tool activity appears here as soon as the agent uses one."
          />
        ) : (
          <div className="space-y-2 p-3">
            {executions.data?.map((execution) => (
              <ToolExecutionCard key={execution.id} execution={execution} />
            ))}
            {(executions.data?.length ?? 0) >= limit && (
              <div className="pt-1 text-center">
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
      </Card>
    </div>
  );
}
