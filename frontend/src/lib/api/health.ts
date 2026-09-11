/** System health and the dashboard aggregate. */

import { request } from "./client";
import type { SystemOverview } from "./types";

export interface HealthResponse {
  status: string;
  version: string;
  environment: string;
  database: { connected: boolean; error: string | null };
  llm: { configured: boolean; model: string };
  tools: { count: number };
}

export const healthApi = {
  check(signal?: AbortSignal): Promise<HealthResponse> {
    return request<HealthResponse>("/health", { signal, timeoutMs: 8_000 });
  },

  /** Counters, component health and agent state in one request. */
  overview(signal?: AbortSignal): Promise<SystemOverview> {
    return request<SystemOverview>("/system/overview", { signal });
  },
};
