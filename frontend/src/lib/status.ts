/**
 * The single mapping from backend status values to how they look.
 *
 * Centralised so a status never renders green on one screen and amber on
 * another, and so adding a status is one edit.
 */

import type {
  AgentPhase,
  ApprovalStatus,
  EventStatus,
  EventType,
  PermissionLevel,
  ProjectStatus,
  RunStatus,
  TaskStatus,
  ToolExecutionStatus,
} from "@/lib/api";

export type Tone = "neutral" | "accent" | "ok" | "warn" | "danger" | "info";

export const TONE_CLASSES: Record<Tone, string> = {
  neutral: "bg-white/5 text-ink-muted border-line",
  accent: "bg-accent-soft text-accent border-accent-line",
  ok: "bg-ok-soft text-ok border-ok/25",
  warn: "bg-warn-soft text-warn border-warn/25",
  danger: "bg-danger-soft text-danger border-danger/25",
  info: "bg-info-soft text-info border-info/25",
};

export const TONE_DOT: Record<Tone, string> = {
  neutral: "bg-ink-faint",
  accent: "bg-accent",
  ok: "bg-ok",
  warn: "bg-warn",
  danger: "bg-danger",
  info: "bg-info",
};

const TASK_TONES: Record<TaskStatus, Tone> = {
  PENDING: "neutral",
  PLANNING: "info",
  RUNNING: "accent",
  WAITING_APPROVAL: "warn",
  COMPLETED: "ok",
  FAILED: "danger",
  CANCELLED: "neutral",
};

const RUN_TONES: Record<RunStatus, Tone> = {
  RUNNING: "accent",
  WAITING_APPROVAL: "warn",
  COMPLETED: "ok",
  FAILED: "danger",
  CANCELLED: "neutral",
};

const PHASE_TONES: Record<AgentPhase, Tone> = {
  IDLE: "neutral",
  UNDERSTANDING: "info",
  LOADING_CONTEXT: "info",
  PLANNING: "info",
  THINKING: "accent",
  RUNNING: "accent",
  USING_TOOL: "accent",
  WAITING_APPROVAL: "warn",
  VERIFYING: "info",
  COMPLETED: "ok",
  FAILED: "danger",
  CANCELLED: "neutral",
};

const PROJECT_TONES: Record<ProjectStatus, Tone> = {
  ACTIVE: "ok",
  PAUSED: "warn",
  ARCHIVED: "neutral",
};

const APPROVAL_TONES: Record<ApprovalStatus, Tone> = {
  PENDING: "warn",
  APPROVED: "ok",
  REJECTED: "danger",
  EXPIRED: "neutral",
};

const TOOL_TONES: Record<ToolExecutionStatus, Tone> = {
  SUCCESS: "ok",
  FAILED: "danger",
  TIMEOUT: "warn",
  DENIED: "warn",
};

const EVENT_TONES: Record<EventStatus, Tone> = {
  info: "neutral",
  running: "accent",
  success: "ok",
  failure: "danger",
  waiting: "warn",
};

/**
 * Permission level colouring — this is a safety signal, so the scale is
 * deliberately monotonic: the more dangerous, the louder.
 */
const PERMISSION_TONES: Record<PermissionLevel, Tone> = {
  READ: "neutral",
  WRITE: "info",
  EXECUTE: "warn",
  DELETE: "warn",
  CRITICAL: "danger",
};

export const taskTone = (status: TaskStatus): Tone =>
  TASK_TONES[status] ?? "neutral";
export const runTone = (status: RunStatus): Tone => RUN_TONES[status] ?? "neutral";
export const phaseTone = (phase: AgentPhase): Tone =>
  PHASE_TONES[phase] ?? "neutral";
export const projectTone = (status: ProjectStatus): Tone =>
  PROJECT_TONES[status] ?? "neutral";
export const approvalTone = (status: ApprovalStatus): Tone =>
  APPROVAL_TONES[status] ?? "neutral";
export const toolTone = (status: ToolExecutionStatus): Tone =>
  TOOL_TONES[status] ?? "neutral";
export const eventTone = (status: EventStatus): Tone =>
  EVENT_TONES[status] ?? "neutral";
export const permissionTone = (level: PermissionLevel): Tone =>
  PERMISSION_TONES[level] ?? "neutral";

export const healthTone = (status: string): Tone =>
  status === "ok" ? "ok" : status === "unconfigured" ? "warn" : "danger";

/** Glyph per event type, so a timeline scans without reading every line. */
export const EVENT_GLYPHS: Partial<Record<EventType, string>> = {
  "agent.started": "▸",
  "context.loaded": "◆",
  "agent.planning": "◇",
  "agent.replanning": "↺",
  "agent.thinking": "∴",
  "tool.started": "▪",
  "tool.completed": "✓",
  "tool.failed": "✕",
  "permission.checked": "⚿",
  "approval.required": "⏳",
  "verification.started": "◎",
  "verification.completed": "✓",
  "verification.failed": "✕",
  "task.completed": "✓",
  "task.failed": "✕",
  "agent.error": "!",
};

export const eventGlyph = (type: EventType): string =>
  EVENT_GLYPHS[type] ?? "•";
