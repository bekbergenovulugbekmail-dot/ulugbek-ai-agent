"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";

import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Select, TextInput } from "@/components/ui/Field";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/States";
import { MEMORY_TYPES, memoryApi, type Memory, type MemoryType } from "@/lib/api";
import { formatRelative, humanize } from "@/lib/format";
import { useDebounced } from "@/lib/hooks/useDebounced";
import { useResource } from "@/lib/hooks/useResource";

const PAGE_SIZE = 25;

function MemoryRow({ memory, score }: { memory: Memory; score?: number }) {
  return (
    <article className="px-5 py-4" data-testid="memory-row">
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <Badge tone="neutral">{humanize(memory.type)}</Badge>
        {memory.tags.map((tag) => (
          <span
            key={tag}
            className="rounded bg-white/5 px-1.5 py-0.5 text-2xs text-ink-faint"
          >
            {tag}
          </span>
        ))}
        {score !== undefined && (
          <span className="font-mono text-2xs text-accent">
            score {score.toFixed(2)}
          </span>
        )}
        <span className="ml-auto font-mono text-2xs text-ink-faint">
          {formatRelative(memory.created_at)}
        </span>
      </div>
      {/* Content is stored redacted by the backend, so it is safe to show. */}
      <p className="whitespace-pre-wrap text-sm leading-relaxed text-ink-muted">
        {memory.content}
      </p>
      <div className="mt-2 flex gap-3 text-2xs text-ink-faint">
        <span>importance {memory.importance.toFixed(2)}</span>
        <span>read {memory.access_count}×</span>
        {memory.source && <span className="font-mono">{memory.source}</span>}
      </div>
    </article>
  );
}

function MemoryView() {
  const params = useSearchParams();
  const projectId = params.get("project") ?? undefined;

  const [query, setQuery] = useState("");
  const [type, setType] = useState<MemoryType | "">("");
  const [limit, setLimit] = useState(PAGE_SIZE);
  const debouncedQuery = useDebounced(query, 350);
  const searching = debouncedQuery.trim().length > 0;

  const listed = useResource(
    (signal) =>
      memoryApi.list(
        { type: type || undefined, project_id: projectId, limit },
        signal,
      ),
    [type, projectId, limit],
    { enabled: !searching },
  );

  const found = useResource(
    (signal) =>
      memoryApi.search(debouncedQuery, { project_id: projectId, limit: 25 }, signal),
    [debouncedQuery, projectId],
    { enabled: searching },
  );

  const state = searching ? found : listed;
  const rows = searching
    ? (found.data ?? []).map((result) => ({
        memory: result.memory,
        score: result.score,
      }))
    : (listed.data ?? []).map((memory) => ({ memory, score: undefined }));

  return (
    <div>
      <PageHeader
        title="Memory"
        description="What the agent knows. Only relevant entries are ever sent to the model."
      />

      <div className="mb-4 flex flex-wrap gap-2">
        <div className="min-w-[16rem] flex-1">
          <label htmlFor="memory-search" className="sr-only">
            Search memory
          </label>
          <TextInput
            id="memory-search"
            value={query}
            placeholder="Search memory…"
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        <label htmlFor="memory-type" className="sr-only">
          Memory type
        </label>
        <Select
          id="memory-type"
          value={type}
          disabled={searching}
          onChange={(event) => {
            setType(event.target.value as MemoryType | "");
            setLimit(PAGE_SIZE);
          }}
        >
          <option value="">All types</option>
          {MEMORY_TYPES.map((option) => (
            <option key={option} value={option}>
              {humanize(option)}
            </option>
          ))}
        </Select>
      </div>

      {state.loading ? (
        <LoadingState rows={4} label="Loading memory" />
      ) : state.error ? (
        <ErrorState error={state.error} onRetry={state.reload} />
      ) : rows.length === 0 ? (
        <Card>
          <EmptyState
            icon="◇"
            title={searching ? "Nothing matched" : "No memories yet"}
            description={
              searching
                ? "Try different words — search ranks by relevance, not exact match."
                : "The agent stores facts, decisions and preferences here as it works."
            }
          />
        </Card>
      ) : (
        <Card className="divide-y divide-line">
          {rows.map(({ memory, score }) => (
            <MemoryRow key={memory.id} memory={memory} score={score} />
          ))}
        </Card>
      )}

      {!searching && rows.length >= limit && (
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

export default function MemoryPage() {
  return (
    <Suspense fallback={<LoadingState rows={4} label="Loading memory" />}>
      <MemoryView />
    </Suspense>
  );
}
