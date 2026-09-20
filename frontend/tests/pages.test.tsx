/** Page-level behaviour: lists, filters, empty and error states. */

import { render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const searchParams = { current: new URLSearchParams() };

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => searchParams.current,
  usePathname: () => "/",
}));

import ProjectsPage from "@/app/projects/page";
import TasksPage from "@/app/tasks/page";
import ApprovalsPage from "@/app/approvals/page";
import { ApiError, approvalApi, healthApi, projectApi, taskApi } from "@/lib/api";

import { makeApproval, makeProject, makeTask } from "./setup-mocks";

beforeEach(() => {
  searchParams.current = new URLSearchParams();
  vi.restoreAllMocks();
  vi.spyOn(healthApi, "overview").mockResolvedValue({
    version: "0.1.0",
    environment: "test",
    healthy: true,
    components: [],
    counters: {
      projects: 1,
      active_projects: 1,
      tasks: 2,
      active_tasks: 1,
      completed_tasks: 1,
      failed_tasks: 0,
      pending_approvals: 0,
      memories: 0,
      runs: 1,
      running_runs: 0,
      tool_executions: 0,
    },
    agent: {
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
    },
    tasks_by_status: { COMPLETED: 1, RUNNING: 1 },
  });
});

describe("Projects page", () => {
  it("lists projects with their status and repository", async () => {
    vi.spyOn(projectApi, "list").mockResolvedValue([
      makeProject(),
      makeProject({
        id: "b",
        name: "Telegram Bot",
        slug: "telegram-bot",
        status: "PAUSED",
      }),
    ]);

    render(<ProjectsPage />);

    const cards = await screen.findAllByTestId("project-card");
    expect(cards).toHaveLength(2);
    expect(screen.getByText("ERP")).toBeInTheDocument();
    expect(screen.getByText("Telegram Bot")).toBeInTheDocument();
    // Scoped to the card, because "PAUSED" is also a filter tab label.
    expect(within(cards[1]).getByText("PAUSED")).toBeInTheDocument();
    expect(within(cards[0]).getByText("ulugbek/erp")).toBeInTheDocument();
  });

  it("guides the operator when there are no projects", async () => {
    vi.spyOn(projectApi, "list").mockResolvedValue([]);

    render(<ProjectsPage />);

    expect(await screen.findByText("No projects yet")).toBeInTheDocument();
  });

  it("shows an actionable error when the backend is down", async () => {
    vi.spyOn(projectApi, "list").mockRejectedValue(
      new ApiError("Could not reach the ULUGBEK AI backend.", {
        code: "network_error",
      }),
    );

    render(<ProjectsPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Cannot reach the backend",
    );
  });
});

describe("Tasks page", () => {
  it("lists tasks with status and progress", async () => {
    vi.spyOn(taskApi, "list").mockResolvedValue([
      makeTask(),
      makeTask({ id: "t2", goal: "Deploy ERP", status: "COMPLETED" }),
    ]);

    render(<TasksPage />);

    expect(await screen.findAllByTestId("task-card")).toHaveLength(2);
    expect(screen.getByText("Check the Telegram project")).toBeInTheDocument();
  });

  it("filters by status", async () => {
    const list = vi.spyOn(taskApi, "list").mockResolvedValue([]);
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();

    render(<TasksPage />);
    await screen.findByText("No tasks here");

    await user.click(screen.getByRole("tab", { name: /FAILED/ }));

    await waitFor(() =>
      expect(list).toHaveBeenCalledWith(
        expect.objectContaining({ status: "FAILED" }),
        expect.anything(),
      ),
    );
  });
});

describe("Approvals page", () => {
  it("shows pending approvals first", async () => {
    vi.spyOn(approvalApi, "list").mockResolvedValue([makeApproval()]);

    render(<ApprovalsPage />);

    expect(await screen.findByTestId("approval-card")).toBeInTheDocument();
    expect(screen.getByText("Waiting for your approval")).toBeInTheDocument();
  });

  it("reassures when nothing is waiting", async () => {
    vi.spyOn(approvalApi, "list").mockResolvedValue([]);

    render(<ApprovalsPage />);

    expect(await screen.findByText("Nothing waiting on you")).toBeInTheDocument();
  });
});

describe("the deployment health route", () => {
  it("answers the exact shape the platform and CI look for", async () => {
    // Railway's healthcheckPath and the pipeline's wait loop both match on
    // `"status": "ok"`. Changing this shape silently breaks a deploy gate.
    const { GET } = await import("@/app/healthz/route");

    const response = GET();

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({
      status: "ok",
      service: "web",
    });
  });
});
