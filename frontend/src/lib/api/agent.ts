/** Agent runs and the agent's live state. */

import { apiUrl, request, type QueryParams } from "./client";
import type {
  AgentRun,
  AgentRunDetail,
  AgentRunResponse,
  AgentStateSnapshot,
  EventPage,
} from "./types";

export interface RunRequest {
  message: string;
  project_id?: string | null;
  task_id?: string | null;
  max_iterations?: number | null;
}

export const agentApi = {
  /**
   * Start a run and return as soon as it has an id.
   *
   * This is what the control centre uses: the work continues on the server and
   * the UI follows it on the event stream, instead of holding a request open.
   */
  start(payload: RunRequest, signal?: AbortSignal): Promise<AgentRunResponse> {
    return request<AgentRunResponse>("/agent/runs", {
      method: "POST",
      body: payload,
      signal,
    });
  },

  /** Run and wait for the final result. Useful for scripts, not for a UI. */
  runSync(payload: RunRequest, signal?: AbortSignal): Promise<AgentRunResponse> {
    return request<AgentRunResponse>("/agent/run", {
      method: "POST",
      body: payload,
      signal,
      timeoutMs: 300_000,
    });
  },

  listRuns(
    params?: { status?: string; limit?: number; offset?: number },
    signal?: AbortSignal,
  ): Promise<AgentRun[]> {
    return request<AgentRun[]>("/agent/runs", {
      query: params as QueryParams,
      signal,
    });
  },

  getRun(runId: string, signal?: AbortSignal): Promise<AgentRunDetail> {
    return request<AgentRunDetail>(`/agent/runs/${runId}`, { signal });
  },

  state(runId?: string | null, signal?: AbortSignal): Promise<AgentStateSnapshot> {
    return request<AgentStateSnapshot>("/events/state", {
      query: { run_id: runId ?? undefined },
      signal,
    });
  },

  runEvents(
    runId: string,
    afterSequence = 0,
    signal?: AbortSignal,
  ): Promise<EventPage> {
    return request<EventPage>(`/events/runs/${runId}`, {
      query: { after_sequence: afterSequence },
      signal,
    });
  },

  /** URL of the SSE stream for a run — opened by `useRunStream`. */
  streamUrl(runId: string, afterSequence = 0): string {
    return apiUrl(`/events/runs/${runId}/stream`, {
      after_sequence: afterSequence,
    });
  },
};
