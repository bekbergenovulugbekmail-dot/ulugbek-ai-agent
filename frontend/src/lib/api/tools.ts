/** Tool registry and execution history. */

import { request, type QueryParams } from "./client";
import type { ToolDescriptor, ToolExecution, ToolExecutionStatus } from "./types";

export const toolApi = {
  list(signal?: AbortSignal): Promise<{ count: number; tools: ToolDescriptor[] }> {
    return request<{ count: number; tools: ToolDescriptor[] }>("/tools", {
      signal,
    });
  },

  executions(
    params?: {
      run_id?: string;
      task_id?: string;
      tool_name?: string;
      service?: string;
      status?: ToolExecutionStatus;
      limit?: number;
      offset?: number;
    },
    signal?: AbortSignal,
  ): Promise<ToolExecution[]> {
    return request<ToolExecution[]>("/tools/executions", {
      query: params as QueryParams,
      signal,
    });
  },
};
