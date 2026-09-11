/** Tasks. */

import { request, type QueryParams } from "./client";
import type { Task, TaskStatus } from "./types";

export const taskApi = {
  list(
    params?: {
      status?: TaskStatus;
      project_id?: string;
      limit?: number;
      offset?: number;
    },
    signal?: AbortSignal,
  ): Promise<Task[]> {
    return request<Task[]>("/tasks", { query: params as QueryParams, signal });
  },

  get(id: string, signal?: AbortSignal): Promise<Task> {
    return request<Task>(`/tasks/${id}`, { signal });
  },

  cancel(id: string, reason?: string, signal?: AbortSignal): Promise<Task> {
    return request<Task>(`/tasks/${id}/cancel`, {
      method: "POST",
      query: { reason },
      signal,
    });
  },
};
