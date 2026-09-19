/**
 * Wire types mirroring the backend schemas.
 *
 * Kept hand-written and narrow on purpose: the UI depends on the fields it
 * renders, so a backend addition never breaks the build, and a removal does.
 */

export type RunStatus =
  | "RUNNING"
  | "WAITING_APPROVAL"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED";

/**
 * Statuses a run never leaves. `WAITING_APPROVAL` is deliberately absent: the
 * run has stopped, but it has not finished, and treating the two alike makes
 * the UI report a paused run as one that produced nothing.
 */
export const TERMINAL_RUN_STATUSES: readonly RunStatus[] = [
  "COMPLETED",
  "FAILED",
  "CANCELLED",
];

export function isTerminalRunStatus(status: RunStatus | null | undefined): boolean {
  return status != null && TERMINAL_RUN_STATUSES.includes(status);
}

export type TaskStatus =
  | "PENDING"
  | "PLANNING"
  | "RUNNING"
  | "WAITING_APPROVAL"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED";

export type ProjectStatus = "ACTIVE" | "PAUSED" | "ARCHIVED";

export type ApprovalStatus = "PENDING" | "APPROVED" | "REJECTED" | "EXPIRED";

export type PermissionLevel =
  | "READ"
  | "WRITE"
  | "EXECUTE"
  | "DELETE"
  | "CRITICAL";

export type ToolExecutionStatus = "SUCCESS" | "FAILED" | "TIMEOUT" | "DENIED";

export type VerificationStatus =
  | "SUCCESS"
  | "FAILURE"
  | "INCONCLUSIVE"
  | "SKIPPED";

export type MemoryType =
  | "USER_CONTEXT"
  | "PROJECT_CONTEXT"
  | "DECISION"
  | "PREFERENCE"
  | "FACT"
  | "TASK_CONTEXT"
  | "CONVERSATION_SUMMARY"
  | "TOOL_RESULT";

export const MEMORY_TYPES: MemoryType[] = [
  "USER_CONTEXT",
  "PROJECT_CONTEXT",
  "DECISION",
  "PREFERENCE",
  "FACT",
  "TASK_CONTEXT",
  "CONVERSATION_SUMMARY",
  "TOOL_RESULT",
];

export const TASK_STATUSES: TaskStatus[] = [
  "PENDING",
  "PLANNING",
  "RUNNING",
  "WAITING_APPROVAL",
  "COMPLETED",
  "FAILED",
  "CANCELLED",
];

export type AgentPhase =
  | "IDLE"
  | "UNDERSTANDING"
  | "LOADING_CONTEXT"
  | "PLANNING"
  | "THINKING"
  | "RUNNING"
  | "USING_TOOL"
  | "WAITING_APPROVAL"
  | "VERIFYING"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED";

export type EventStatus = "info" | "running" | "success" | "failure" | "waiting";

export type EventType =
  | "agent.started"
  | "agent.planning"
  | "agent.replanning"
  | "agent.thinking"
  | "agent.error"
  | "context.loaded"
  | "tool.started"
  | "tool.completed"
  | "tool.failed"
  | "permission.checked"
  | "approval.required"
  | "verification.started"
  | "verification.completed"
  | "verification.failed"
  | "task.completed"
  | "task.failed";

export interface AgentEvent {
  id: string;
  run_id: string;
  task_id: string | null;
  project_id: string | null;
  sequence: number;
  iteration: number | null;
  type: EventType;
  status: EventStatus;
  timestamp: string;
  safe_message: string;
  subject: string | null;
  metadata: Record<string, unknown>;
}

export interface EventPage {
  events: AgentEvent[];
  cursor: number | null;
  has_more: boolean;
}

export interface AgentStateSnapshot {
  phase: AgentPhase;
  label: string;
  busy: boolean;
  run_id: string | null;
  run_status: RunStatus | null;
  task_id: string | null;
  task_status: TaskStatus | null;
  goal: string | null;
  project_id: string | null;
  iteration: number;
  detail: string | null;
  pending_approval_id: string | null;
  updated_at: string | null;
}

export interface ApprovalRequestInfo {
  approval_id: string;
  tool_name: string;
  permission: string;
  reason: string;
  tool_arguments: Record<string, unknown>;
}

export interface AgentRunResponse {
  run_id: string;
  task_id: string | null;
  project_id: string | null;
  status: RunStatus;
  task_status: TaskStatus | null;
  output: string | null;
  error: string | null;
  iterations: number;
  replans: number;
  tools_used: string[];
  verification: { status: VerificationStatus; reason: string } | null;
  approval: ApprovalRequestInfo | null;
  token_usage: Record<string, number>;
}

export interface AgentRun {
  id: string;
  input: string;
  status: RunStatus;
  output: string | null;
  error: string | null;
  iterations: number;
  replans: number;
  model: string | null;
  token_usage: Record<string, number>;
  task_id: string | null;
  project_id: string | null;
  user_id: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export interface AgentStep {
  id: string;
  sequence: number;
  iteration: number | null;
  type: string;
  summary: string;
  payload: Record<string, unknown>;
  success: boolean | null;
  created_at: string;
}

export interface AgentRunDetail extends AgentRun {
  steps: AgentStep[];
}

export interface Project {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  status: ProjectStatus;
  repository: string | null;
  environment: string | null;
  owner_id: string | null;
  keywords: string[];
  integrations: Record<string, unknown>;
  extra: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface Task {
  id: string;
  goal: string;
  status: TaskStatus;
  priority: "LOW" | "NORMAL" | "HIGH" | "URGENT";
  steps: Array<Record<string, unknown>>;
  current_step: number;
  result: string | null;
  error: string | null;
  extra: Record<string, unknown>;
  project_id: string | null;
  user_id: string | null;
  parent_task_id: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface Memory {
  id: string;
  type: MemoryType;
  content: string;
  summary: string | null;
  source: string | null;
  importance: number;
  tags: string[];
  extra: Record<string, unknown>;
  user_id: string | null;
  project_id: string | null;
  task_id: string | null;
  access_count: number;
  last_accessed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface MemorySearchResult {
  memory: Memory;
  score: number;
}

export interface Approval {
  id: string;
  status: ApprovalStatus;
  tool_name: string;
  tool_arguments: Record<string, unknown>;
  permission: PermissionLevel;
  reason: string;
  goal: string | null;
  decided_by: string | null;
  decision_note: string | null;
  decided_at: string | null;
  expires_at: string | null;
  agent_run_id: string | null;
  task_id: string | null;
  project_id: string | null;
  tool_call_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface ToolDescriptor {
  name: string;
  description: string;
  service: string | null;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  permission: PermissionLevel;
  timeout_seconds: number | null;
  idempotent: boolean;
}

export interface ToolExecution {
  id: string;
  tool_name: string;
  service: string | null;
  status: ToolExecutionStatus;
  permission: PermissionLevel;
  arguments: Record<string, unknown>;
  output: Record<string, unknown> | null;
  error: string | null;
  duration_ms: number;
  verification_status: VerificationStatus | null;
  verification_reason: string | null;
  agent_run_id: string | null;
  task_id: string | null;
  created_at: string;
}

export interface ComponentHealth {
  name: string;
  status: "ok" | "degraded" | "unconfigured";
  detail: string | null;
}

export interface SystemCounters {
  projects: number;
  active_projects: number;
  tasks: number;
  active_tasks: number;
  completed_tasks: number;
  failed_tasks: number;
  pending_approvals: number;
  memories: number;
  runs: number;
  running_runs: number;
  tool_executions: number;
}

export interface SystemOverview {
  version: string;
  environment: string;
  healthy: boolean;
  components: ComponentHealth[];
  counters: SystemCounters;
  agent: AgentStateSnapshot;
  tasks_by_status: Record<string, number>;
}

export interface ProjectOverview {
  project: Project;
  tasks: Task[];
  memories: Memory[];
  activity: AgentEvent[];
  integrations: string[];
}
