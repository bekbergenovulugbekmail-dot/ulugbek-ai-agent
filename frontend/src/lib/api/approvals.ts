/** Human-in-the-loop approvals. */

import { request, type QueryParams } from "./client";
import type { AgentRunResponse, Approval, ApprovalStatus } from "./types";

export interface ApprovalDecision {
  decided_by?: string | null;
  note?: string | null;
}

export const approvalApi = {
  list(
    params?: { status?: ApprovalStatus; limit?: number; offset?: number },
    signal?: AbortSignal,
  ): Promise<Approval[]> {
    return request<Approval[]>("/approvals", {
      query: params as QueryParams,
      signal,
    });
  },

  get(id: string, signal?: AbortSignal): Promise<Approval> {
    return request<Approval>(`/approvals/${id}`, { signal });
  },

  /**
   * Approve and let the run continue in the background.
   *
   * `background=true` matters here: resuming can take as long as the rest of
   * the run, and the operator should watch it on the timeline rather than on a
   * spinner.
   */
  approve(
    id: string,
    decision: ApprovalDecision = {},
    signal?: AbortSignal,
  ): Promise<AgentRunResponse> {
    return request<AgentRunResponse>(`/approvals/${id}/approve`, {
      method: "POST",
      query: { background: true },
      body: decision,
      signal,
    });
  },

  reject(
    id: string,
    decision: ApprovalDecision = {},
    signal?: AbortSignal,
  ): Promise<AgentRunResponse> {
    return request<AgentRunResponse>(`/approvals/${id}/reject`, {
      method: "POST",
      query: { background: true },
      body: decision,
      signal,
    });
  },
};
