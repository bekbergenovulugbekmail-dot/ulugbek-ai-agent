/**
 * The API surface the UI is allowed to use.
 *
 * Components import from here — never `fetch` directly — so the base URL,
 * error handling, timeouts and the future auth header live in exactly one
 * place.
 */

export { agentApi, type RunRequest } from "./agent";
export { approvalApi, type ApprovalDecision } from "./approvals";
export { eventApi, type EventQuery } from "./events";
export { healthApi, type HealthResponse } from "./health";
export { memoryApi } from "./memory";
export { projectApi, type ProjectCreate } from "./projects";
export { taskApi } from "./tasks";
export { toolApi } from "./tools";
export {
  API_BASE_URL,
  ApiError,
  apiUrl,
  buildQuery,
  request,
  setAuthTokenProvider,
} from "./client";
export * from "./types";
