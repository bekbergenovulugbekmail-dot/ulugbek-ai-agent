/** Projects. */

import { request, type QueryParams } from "./client";
import type { Project, ProjectOverview, ProjectStatus } from "./types";

export interface ProjectCreate {
  name: string;
  description?: string | null;
  status?: ProjectStatus;
  repository?: string | null;
  environment?: string | null;
  keywords?: string[];
}

export const projectApi = {
  list(
    params?: { status?: ProjectStatus; limit?: number; offset?: number },
    signal?: AbortSignal,
  ): Promise<Project[]> {
    return request<Project[]>("/projects", {
      query: params as QueryParams,
      signal,
    });
  },

  get(id: string, signal?: AbortSignal): Promise<Project> {
    return request<Project>(`/projects/${id}`, { signal });
  },

  overview(id: string, signal?: AbortSignal): Promise<ProjectOverview> {
    return request<ProjectOverview>(`/projects/${id}/overview`, { signal });
  },

  create(payload: ProjectCreate, signal?: AbortSignal): Promise<Project> {
    return request<Project>("/projects", {
      method: "POST",
      body: payload,
      signal,
    });
  },

  update(
    id: string,
    payload: Partial<ProjectCreate>,
    signal?: AbortSignal,
  ): Promise<Project> {
    return request<Project>(`/projects/${id}`, {
      method: "PATCH",
      body: payload,
      signal,
    });
  },
};
