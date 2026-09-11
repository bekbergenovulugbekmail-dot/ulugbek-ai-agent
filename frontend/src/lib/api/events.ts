/** The global activity feed. */

import { request, type QueryParams } from "./client";
import type { EventPage } from "./types";

export interface EventQuery {
  run_id?: string;
  task_id?: string;
  project_id?: string;
  type?: string[];
  limit?: number;
  offset?: number;
}

export const eventApi = {
  list(params?: EventQuery, signal?: AbortSignal): Promise<EventPage> {
    return request<EventPage>("/events", {
      query: params as QueryParams,
      signal,
    });
  },
};
