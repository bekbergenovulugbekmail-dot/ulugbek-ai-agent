"use client";

import { PageHeader } from "@/components/layout/PageHeader";
import { HealthList } from "@/components/system/HealthIndicator";
import { Badge } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import { ErrorState, LoadingState } from "@/components/ui/States";
import { API_BASE_URL, healthApi } from "@/lib/api";
import { useResource } from "@/lib/hooks/useResource";
import { permissionTone } from "@/lib/status";

const PERMISSION_MODEL = [
  { level: "READ", behaviour: "Runs automatically" },
  { level: "WRITE", behaviour: "Runs automatically (configurable)" },
  { level: "EXECUTE", behaviour: "Requires your approval" },
  { level: "DELETE", behaviour: "Requires your approval" },
  { level: "CRITICAL", behaviour: "Always requires your approval" },
] as const;

export default function SettingsPage() {
  const overview = useResource((signal) => healthApi.overview(signal), [], {
    pollMs: 15_000,
  });
  const health = useResource((signal) => healthApi.check(signal), []);

  return (
    <div className="space-y-5">
      <PageHeader
        title="Settings"
        description="How this console is connected, and what the agent is allowed to do."
      />

      <Card>
        <CardHeader title="Connection" />
        <dl className="divide-y divide-line">
          <div className="flex items-center justify-between gap-4 px-5 py-3">
            <dt className="text-sm text-ink-muted">API base URL</dt>
            <dd className="truncate font-mono text-xs text-ink">
              {API_BASE_URL}
            </dd>
          </div>
          <div className="flex items-center justify-between gap-4 px-5 py-3">
            <dt className="text-sm text-ink-muted">Backend version</dt>
            <dd className="font-mono text-xs text-ink">
              {overview.data?.version ?? "—"}
            </dd>
          </div>
          <div className="flex items-center justify-between gap-4 px-5 py-3">
            <dt className="text-sm text-ink-muted">Environment</dt>
            <dd className="font-mono text-xs text-ink">
              {overview.data?.environment ?? "—"}
            </dd>
          </div>
          <div className="flex items-center justify-between gap-4 px-5 py-3">
            <dt className="text-sm text-ink-muted">Model</dt>
            <dd className="font-mono text-xs text-ink">
              {health.data?.llm.model ?? "—"}
            </dd>
          </div>
          <div className="flex items-center justify-between gap-4 px-5 py-3">
            <dt className="text-sm text-ink-muted">Authentication</dt>
            <dd>
              <Badge tone="warn">Not enabled yet</Badge>
            </dd>
          </div>
        </dl>
        <p className="border-t border-line px-5 py-3 text-xs leading-relaxed text-ink-faint">
          API keys live only on the backend, in its environment. This console
          never receives, stores or displays a credential.
        </p>
      </Card>

      <Card>
        <CardHeader title="System health" />
        <div className="px-5 py-4">
          {overview.loading ? (
            <LoadingState rows={2} label="Checking" />
          ) : overview.error ? (
            <ErrorState error={overview.error} onRetry={overview.reload} />
          ) : (
            <HealthList components={overview.data?.components ?? []} />
          )}
        </div>
      </Card>

      <Card>
        <CardHeader
          title="Permission model"
          subtitle="What the agent may do on its own, and what it must ask about"
        />
        <ul className="divide-y divide-line">
          {PERMISSION_MODEL.map((row) => (
            <li
              key={row.level}
              className="flex items-center justify-between gap-4 px-5 py-3"
            >
              <Badge tone={permissionTone(row.level)}>{row.level}</Badge>
              <span className="text-sm text-ink-muted">{row.behaviour}</span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
