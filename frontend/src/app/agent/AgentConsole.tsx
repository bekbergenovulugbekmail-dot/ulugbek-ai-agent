"use client";

/**
 * The agent console — the screen this whole application exists for.
 *
 * Layout: conversation on the left, live activity on the right. Sending starts
 * a run in the background and subscribes to its event stream, so the operator
 * watches the work happen instead of staring at a spinner.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";

import { ActivityTimeline } from "@/components/agent/ActivityTimeline";
import { AgentStatus } from "@/components/agent/AgentStatus";
import type { ChatEntry } from "@/components/agent/ChatMessage";
import { ChatMessage } from "@/components/agent/ChatMessage";
import { CommandInput } from "@/components/agent/CommandInput";
import { ApprovalCard } from "@/components/cards/ApprovalCard";
import { ToolExecutionCard } from "@/components/cards/ToolExecutionCard";
import { Card, CardHeader } from "@/components/ui/Card";
import { ErrorBoundary } from "@/components/ui/ErrorBoundary";
import { Badge } from "@/components/ui/Badge";
import { EmptyState, ErrorState } from "@/components/ui/States";
import {
  ApiError,
  agentApi,
  approvalApi,
  projectApi,
  toolApi,
  type AgentStateSnapshot,
  type Approval,
} from "@/lib/api";
import { truncate } from "@/lib/format";
import { useResource } from "@/lib/hooks/useResource";
import { useRunStream } from "@/lib/hooks/useRunStream";

let localId = 0;
const nextId = () => `local-${++localId}`;

export function AgentConsole() {
  const router = useRouter();
  const params = useSearchParams();
  const runFromUrl = params.get("run");
  const draft = params.get("draft");

  const [runId, setRunId] = useState<string | null>(runFromUrl);
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [sending, setSending] = useState(false);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | undefined>();
  const [approval, setApproval] = useState<Approval | undefined>();

  const answered = useRef<Set<string>>(new Set());
  const bottomRef = useRef<HTMLDivElement>(null);

  const stream = useRunStream(runId);
  const projects = useResource((signal) => projectApi.list({ limit: 50 }, signal), []);

  // The state the header shows: live from the stream, or the last known state.
  const fallbackState = useResource(
    (signal) => agentApi.state(runId, signal),
    [runId],
    { pollMs: stream.state ? undefined : 8_000 },
  );
  const latest: AgentStateSnapshot | undefined = stream.state ?? fallbackState.data;

  // With no run open, the console is idle — showing the *previous* run's
  // terminal phase here would read as if this session had just failed.
  const state: AgentStateSnapshot | undefined = runId ? latest : undefined;
  const lastRun = !runId && latest?.run_id ? latest : undefined;

  const busy = Boolean(state?.busy) || sending;

  const executions = useResource(
    (signal) => toolApi.executions({ run_id: runId ?? undefined, limit: 20 }, signal),
    [runId, stream.events.length],
    { enabled: Boolean(runId) },
  );

  useEffect(() => {
    if (runFromUrl && runFromUrl !== runId) setRunId(runFromUrl);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runFromUrl]);

  /** Rehydrate the conversation when a run is opened from a link. */
  useEffect(() => {
    if (!runId || entries.length > 0) return;
    let cancelled = false;
    void (async () => {
      try {
        const run = await agentApi.getRun(runId);
        if (cancelled) return;
        const restored: ChatEntry[] = [
          {
            id: nextId(),
            role: "user",
            text: run.input,
            timestamp: run.created_at,
          },
        ];
        if (run.output) {
          answered.current.add(runId);
          restored.push({
            id: nextId(),
            role: "agent",
            text: run.output,
            timestamp: run.finished_at ?? run.created_at,
          });
        } else if (run.error) {
          answered.current.add(runId);
          restored.push({
            id: nextId(),
            role: "agent",
            text: run.error,
            timestamp: run.finished_at ?? run.created_at,
            failed: true,
          });
        }
        setEntries(restored);
      } catch {
        /* a missing run simply leaves the console empty */
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  /** When a run settles, append its answer to the conversation exactly once. */
  useEffect(() => {
    if (!runId || !stream.finished || answered.current.has(runId)) return;
    answered.current.add(runId);
    void (async () => {
      try {
        const run = await agentApi.getRun(runId);
        setEntries((current) => [
          ...current,
          {
            id: nextId(),
            role: "agent",
            text:
              run.output ??
              run.error ??
              "The run finished without producing an answer.",
            timestamp: run.finished_at ?? new Date().toISOString(),
            failed: run.status === "FAILED",
          },
        ]);
      } catch {
        /* the timeline still shows what happened */
      }
    })();
  }, [runId, stream.finished]);

  /** Surface the approval inline the moment the run pauses for one. */
  useEffect(() => {
    const approvalId = state?.pending_approval_id;
    if (!approvalId) {
      setApproval(undefined);
      return;
    }
    if (approval?.id === approvalId) return;
    void approvalApi
      .get(approvalId)
      .then(setApproval)
      .catch(() => setApproval(undefined));
  }, [state?.pending_approval_id, approval?.id]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [entries.length, stream.events.length]);

  const send = useCallback(
    async (message: string) => {
      setSending(true);
      setError(undefined);
      setEntries((current) => [
        ...current,
        {
          id: nextId(),
          role: "user",
          text: message,
          timestamp: new Date().toISOString(),
        },
      ]);
      try {
        const response = await agentApi.start({
          message,
          project_id: projectId,
        });
        setRunId(response.run_id);
        router.replace(`/agent?run=${response.run_id}`, { scroll: false });
      } catch (caught) {
        const failure =
          caught instanceof ApiError
            ? caught
            : new ApiError("The agent could not be started.");
        setError(failure);
        setEntries((current) => [
          ...current,
          {
            id: nextId(),
            role: "system",
            text: failure.message,
            timestamp: new Date().toISOString(),
          },
        ]);
      } finally {
        setSending(false);
      }
    },
    [projectId, router],
  );

  // A command carried over from the dashboard when starting the run failed.
  const draftSent = useRef(false);
  useEffect(() => {
    if (draft && !draftSent.current) {
      draftSent.current = true;
      void send(draft);
    }
  }, [draft, send]);

  const runningTools = useMemo(
    () => (executions.data ?? []).filter((item) => item.agent_run_id === runId),
    [executions.data, runId],
  );

  return (
    <div className="space-y-5">
      <Card>
        <div className="flex flex-wrap items-center gap-3 px-5 py-4">
          <AgentStatus state={state} />
          {stream.transport !== "idle" && (
            <Badge tone="neutral" className="ml-auto">
              {stream.transport === "sse" ? "Live" : "Polling"}
            </Badge>
          )}
        </div>
        {lastRun && (
          <div className="flex flex-wrap items-center gap-2 border-t border-line px-5 py-2.5 text-xs text-ink-faint">
            <span>
              Last run: {lastRun.label.toLowerCase()}
              {lastRun.goal ? ` — ${truncate(lastRun.goal, 70)}` : ""}
            </span>
            <Link
              href={`/agent?run=${lastRun.run_id}`}
              className="ml-auto text-accent hover:underline"
            >
              Open →
            </Link>
          </div>
        )}
      </Card>

      <div className="grid gap-5 lg:grid-cols-5">
        {/* Conversation */}
        <div className="flex min-w-0 flex-col gap-4 lg:col-span-3">
          <Card className="flex min-h-[22rem] flex-col">
            <CardHeader
              title="Conversation"
              subtitle={
                runId ? (state?.goal ?? "Working…") : "Give the agent a task"
              }
            />
            <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
              {entries.length === 0 ? (
                <EmptyState
                  icon="◈"
                  title="No conversation yet"
                  description="Ask the agent to check a project, look something up, or plan a change. You will see every step it takes."
                />
              ) : (
                entries.map((entry) => (
                  <ChatMessage key={entry.id} entry={entry} />
                ))
              )}
              {busy && (
                <div className="flex items-center gap-2 pl-10 text-xs text-ink-faint">
                  <span className="h-1.5 w-1.5 animate-pulse-soft rounded-full bg-accent" />
                  {state?.label ?? "Working"}…
                </div>
              )}
              <div ref={bottomRef} />
            </div>
          </Card>

          {approval && approval.status === "PENDING" && (
            <ApprovalCard
              approval={approval}
              onDecided={() => {
                setApproval(undefined);
                answered.current.delete(runId ?? "");
                fallbackState.reload();
              }}
            />
          )}

          {error && <ErrorState error={error} />}

          <CommandInput
            onSubmit={send}
            busy={busy}
            projects={projects.data ?? []}
            projectId={projectId}
            onProjectChange={setProjectId}
            autoFocus
          />
        </div>

        {/* Live activity */}
        <div className="min-w-0 space-y-5 lg:col-span-2">
          <ErrorBoundary label="Activity">
            <Card>
              <CardHeader
                title="Activity"
                subtitle={
                  runId ? `Run ${runId.slice(0, 8)}` : "Nothing running"
                }
              />
              <div className="max-h-[28rem] overflow-y-auto p-3">
                <ActivityTimeline
                  events={stream.events}
                  live={busy}
                  dense
                  emptyLabel="No activity yet"
                />
              </div>
            </Card>
          </ErrorBoundary>

          {runningTools.length > 0 && (
            <Card>
              <CardHeader title="Tool activity" />
              <div className="space-y-2 p-3">
                {runningTools.map((execution) => (
                  <ToolExecutionCard
                    key={execution.id}
                    execution={execution}
                    compact
                  />
                ))}
              </div>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
