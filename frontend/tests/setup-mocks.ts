/** Shared test doubles: Next navigation and API fixtures. */

import { vi } from "vitest";

import type {
  AgentEvent,
  AgentStateSnapshot,
  Approval,
  Project,
  Task,
} from "@/lib/api";

export const routerMock = {
  push: vi.fn(),
  replace: vi.fn(),
  refresh: vi.fn(),
  back: vi.fn(),
};

export function mockNextNavigation(searchParams: Record<string, string> = {}) {
  vi.mock("next/navigation", () => ({
    useRouter: () => routerMock,
    useSearchParams: () => new URLSearchParams(searchParamsHolder.current),
    usePathname: () => "/agent",
  }));
  searchParamsHolder.current = searchParams;
}

export const searchParamsHolder: { current: Record<string, string> } = {
  current: {},
};

export function makeProject(overrides: Partial<Project> = {}): Project {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    name: "ERP",
    slug: "erp",
    description: "Warehouse and invoicing",
    status: "ACTIVE",
    repository: "ulugbek/erp",
    environment: "staging",
    owner_id: null,
    keywords: ["invoice"],
    integrations: { github: {} },
    extra: {},
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

export function makeTask(overrides: Partial<Task> = {}): Task {
  return {
    id: "22222222-2222-2222-2222-222222222222",
    goal: "Check the Telegram project",
    status: "RUNNING",
    priority: "NORMAL",
    steps: [{ index: 0, description: "Inspect", status: "PENDING" }],
    current_step: 0,
    result: null,
    error: null,
    extra: {},
    project_id: null,
    user_id: null,
    parent_task_id: null,
    started_at: new Date().toISOString(),
    finished_at: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

export function makeApproval(overrides: Partial<Approval> = {}): Approval {
  return {
    id: "33333333-3333-3333-3333-333333333333",
    status: "PENDING",
    tool_name: "deploy",
    tool_arguments: { environment: "production", api_key: "***REDACTED***" },
    permission: "CRITICAL",
    reason: "CRITICAL actions require explicit human approval.",
    goal: "Deploy the telegram bot",
    decided_by: null,
    decision_note: null,
    decided_at: null,
    expires_at: null,
    agent_run_id: "44444444-4444-4444-4444-444444444444",
    task_id: null,
    project_id: null,
    tool_call_id: "toolu_1",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

export function makeEvent(overrides: Partial<AgentEvent> = {}): AgentEvent {
  return {
    id: crypto.randomUUID(),
    run_id: "44444444-4444-4444-4444-444444444444",
    task_id: null,
    project_id: null,
    sequence: 1,
    iteration: 1,
    type: "tool.completed",
    status: "success",
    timestamp: new Date().toISOString(),
    safe_message: "Tool calculate: ok",
    subject: "calculate",
    metadata: {},
    ...overrides,
  };
}

export function makeState(
  overrides: Partial<AgentStateSnapshot> = {},
): AgentStateSnapshot {
  return {
    phase: "IDLE",
    label: "Idle",
    busy: false,
    run_id: null,
    run_status: null,
    task_id: null,
    task_status: null,
    goal: null,
    project_id: null,
    iteration: 0,
    detail: null,
    pending_approval_id: null,
    updated_at: null,
    ...overrides,
  };
}
