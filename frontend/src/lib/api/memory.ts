/** Long-term memory. */

import { request, type QueryParams } from "./client";
import type { Memory, MemorySearchResult, MemoryType } from "./types";

export const memoryApi = {
  list(
    params?: {
      type?: MemoryType;
      project_id?: string;
      limit?: number;
      offset?: number;
    },
    signal?: AbortSignal,
  ): Promise<Memory[]> {
    return request<Memory[]>("/memory", {
      query: params as QueryParams,
      signal,
    });
  },

  search(
    query: string,
    params?: { project_id?: string; limit?: number },
    signal?: AbortSignal,
  ): Promise<MemorySearchResult[]> {
    return request<MemorySearchResult[]>("/memory/search", {
      query: { q: query, ...(params as QueryParams) },
      signal,
    });
  },

  remove(id: string, signal?: AbortSignal): Promise<void> {
    return request<void>(`/memory/${id}`, { method: "DELETE", signal });
  },
};
