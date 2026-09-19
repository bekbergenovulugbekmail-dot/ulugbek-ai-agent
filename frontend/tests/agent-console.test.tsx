/** The agent console: sending a command, following it live, and failing well. */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const routerMock = { push: vi.fn(), replace: vi.fn() };
const searchParams = { current: new URLSearchParams() };

vi.mock("next/navigation", () => ({
  useRouter: () => routerMock,
  useSearchParams: () => searchParams.current,
  usePathname: () => "/agent",
}));

// The console subscribes to a live stream; the test drives the polling path,
// which carries the same data through the same reducer.
vi.stubGlobal("EventSource", undefined);

import { AgentConsole } from "@/app/agent/AgentConsole";
import { ApiError, agentApi, approvalApi, projectApi, toolApi } from "@/lib/api";

import { makeApproval, makeEvent, makeProject, makeState } from "./setup-mocks";

const RUN_ID = "44444444-4444-4444-4444-444444444444";

function stubBaseline() {
  vi.spyOn(projectApi, "list").mockResolvedValue([makeProject()]);
  vi.spyOn(toolApi, "executions").mockResolvedValue([]);
  vi.spyOn(agentApi, "runEvents").mockResolvedValue({
    events: [],
    cursor: null,
    has_more: false,
  });
  vi.spyOn(agentApi, "state").mockResolvedValue(makeState());
}

beforeEach(() => {
  vi.restoreAllMocks();
  routerMock.push.mockReset();
  routerMock.replace.mockReset();
  searchParams.current = new URLSearchParams();
  stubBaseline();
});

describe("AgentConsole", () => {
  it("starts empty, with a prompt to give the agent work", async () => {
    render(<AgentConsole />);

    expect(await screen.findByText("No conversation yet")).toBeInTheDocument();
    expect(screen.getByTestId("agent-phase")).toHaveTextContent("IDLE");
  });

  it("sends a command, echoes it, and starts a background run", async () => {
    const start = vi.spyOn(agentApi, "start").mockResolvedValue({
      run_id: RUN_ID,
      task_id: null,
      project_id: null,
      status: "RUNNING",
      task_status: "RUNNING",
      output: null,
      error: null,
      iterations: 0,
      replans: 0,
      tools_used: [],
      verification: null,
      approval: null,
      token_usage: {},
    });
    const user = userEvent.setup();

    render(<AgentConsole />);
    await user.type(
      screen.getByLabelText("Command for the agent"),
      "Telegram loyihamni tekshir",
    );
    await user.click(screen.getByRole("button", { name: /send/i }));

    // The operator's message appears immediately — no waiting on the backend.
    expect(
      await screen.findByText("Telegram loyihamni tekshir"),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(start).toHaveBeenCalledWith({
        message: "Telegram loyihamni tekshir",
        project_id: null,
      }),
    );
    // The run id goes into the URL so the view is shareable and reloadable.
    await waitFor(() =>
      expect(routerMock.replace).toHaveBeenCalledWith(
        `/agent?run=${RUN_ID}`,
        expect.anything(),
      ),
    );
  });

  it("streams the activity of a run opened from a link", async () => {
    searchParams.current = new URLSearchParams({ run: RUN_ID });
    vi.spyOn(agentApi, "getRun").mockResolvedValue({
      id: RUN_ID,
      input: "Telegram loyihamni tekshir",
      status: "RUNNING",
      output: null,
      error: null,
      iterations: 1,
      replans: 0,
      model: "claude-opus-5",
      token_usage: {},
      task_id: null,
      project_id: null,
      user_id: null,
      started_at: new Date().toISOString(),
      finished_at: null,
      created_at: new Date().toISOString(),
      steps: [],
    });
    vi.spyOn(agentApi, "runEvents").mockResolvedValue({
      events: [
        makeEvent({ sequence: 1, type: "agent.started", safe_message: "Request received" }),
        makeEvent({
          sequence: 2,
          type: "context.loaded",
          safe_message: "Context loaded",
        }),
        makeEvent({
          sequence: 3,
          type: "tool.started",
          status: "running",
          safe_message: "Tool requested: project_list",
        }),
      ],
      cursor: 3,
      has_more: false,
    });
    vi.spyOn(agentApi, "state").mockResolvedValue(
      makeState({
        phase: "USING_TOOL",
        label: "Using a tool",
        busy: true,
        run_id: RUN_ID,
        run_status: "RUNNING",
        goal: "Telegram loyihamni tekshir",
      }),
    );

    render(<AgentConsole />);

    expect(await screen.findByText("Request received")).toBeInTheDocument();
    expect(screen.getByText("Context loaded")).toBeInTheDocument();
    expect(screen.getByText("Tool requested: project_list")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId("agent-phase")).toHaveTextContent("USING TOOL"),
    );
  });

  it("appends the final answer once the run settles", async () => {
    searchParams.current = new URLSearchParams({ run: RUN_ID });
    vi.spyOn(agentApi, "getRun").mockResolvedValue({
      id: RUN_ID,
      input: "Telegram loyihamni tekshir",
      status: "COMPLETED",
      output: "Telegram bot production holatda.",
      error: null,
      iterations: 2,
      replans: 0,
      model: "claude-opus-5",
      token_usage: {},
      task_id: null,
      project_id: null,
      user_id: null,
      started_at: new Date().toISOString(),
      finished_at: new Date().toISOString(),
      created_at: new Date().toISOString(),
      steps: [],
    });
    vi.spyOn(agentApi, "state").mockResolvedValue(
      makeState({
        phase: "COMPLETED",
        label: "Completed",
        busy: false,
        run_id: RUN_ID,
        run_status: "COMPLETED",
      }),
    );

    render(<AgentConsole />);

    expect(
      await screen.findByText("Telegram bot production holatda."),
    ).toBeInTheDocument();
    expect(screen.getByText("Telegram loyihamni tekshir")).toBeInTheDocument();
  });

  it("does not attribute a finished run's outcome to the next one", async () => {
    // The regression: starting a second run in the same session showed the
    // previous run's terminal state against the new run, which had not
    // produced anything yet — "the run finished without producing an answer",
    // one second after sending, while the tools were still running.
    const SECOND_RUN = "55555555-5555-5555-5555-555555555555";
    searchParams.current = new URLSearchParams({ run: RUN_ID });

    vi.spyOn(agentApi, "getRun").mockImplementation(async (id) =>
      id === RUN_ID
        ? {
            id: RUN_ID,
            input: "Birinchi buyruq",
            status: "COMPLETED",
            output: "Birinchi javob.",
            error: null,
            iterations: 2,
            replans: 0,
            model: "claude-opus-5",
            token_usage: {},
            task_id: null,
            project_id: null,
            user_id: null,
            started_at: new Date().toISOString(),
            finished_at: new Date().toISOString(),
            created_at: new Date().toISOString(),
            steps: [],
          }
        : {
            id: SECOND_RUN,
            input: "Ikkinchi buyruq",
            status: "RUNNING",
            output: null,
            error: null,
            iterations: 0,
            replans: 0,
            model: "claude-opus-5",
            token_usage: {},
            task_id: null,
            project_id: null,
            user_id: null,
            started_at: new Date().toISOString(),
            finished_at: null,
            created_at: new Date().toISOString(),
            steps: [],
          },
    );
    const stateSpy = vi.spyOn(agentApi, "state").mockImplementation(async (id) =>
      id === SECOND_RUN
        ? makeState({
            phase: "USING_TOOL",
            label: "Using a tool",
            busy: true,
            run_id: SECOND_RUN,
            run_status: "RUNNING",
          })
        : makeState({
            phase: "COMPLETED",
            label: "Completed",
            busy: false,
            run_id: RUN_ID,
            run_status: "COMPLETED",
          }),
    );
    vi.spyOn(agentApi, "start").mockResolvedValue({
      run_id: SECOND_RUN,
      task_id: null,
      project_id: null,
      status: "RUNNING",
      task_status: "RUNNING",
      output: null,
      error: null,
      iterations: 0,
      replans: 0,
      tools_used: [],
      verification: null,
      approval: null,
      token_usage: {},
    });

    const user = userEvent.setup();
    render(<AgentConsole />);
    expect(await screen.findByText("Birinchi javob.")).toBeInTheDocument();

    await user.type(
      screen.getByLabelText("Command for the agent"),
      "Ikkinchi buyruq",
    );
    await user.click(screen.getByRole("button", { name: /send/i }));

    // The console has switched to the new run and polled its state...
    await waitFor(() =>
      expect(stateSpy.mock.calls.some(([id]) => id === SECOND_RUN)).toBe(true),
    );
    await waitFor(() =>
      expect(screen.getByTestId("agent-phase")).toHaveTextContent("USING TOOL"),
    );

    // ...and has said nothing about a run that is still working.
    expect(
      screen.queryByText(/finished without producing an answer/i),
    ).not.toBeInTheDocument();
  });

  it("shows an approval inline when the run pauses for one", async () => {
    searchParams.current = new URLSearchParams({ run: RUN_ID });
    vi.spyOn(agentApi, "getRun").mockResolvedValue({
      id: RUN_ID,
      input: "Deploy it",
      status: "WAITING_APPROVAL",
      output: null,
      error: null,
      iterations: 1,
      replans: 0,
      model: null,
      token_usage: {},
      task_id: null,
      project_id: null,
      user_id: null,
      started_at: null,
      finished_at: null,
      created_at: new Date().toISOString(),
      steps: [],
    });
    const approval = makeApproval();
    vi.spyOn(agentApi, "state").mockResolvedValue(
      makeState({
        phase: "WAITING_APPROVAL",
        label: "Waiting for your approval",
        busy: true,
        run_id: RUN_ID,
        run_status: "WAITING_APPROVAL",
        pending_approval_id: approval.id,
      }),
    );
    vi.spyOn(approvalApi, "get").mockResolvedValue(approval);

    render(<AgentConsole />);

    expect(await screen.findByTestId("approval-card")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /approve/i })).toBeInTheDocument();
  });

  it("reports a failed start without losing the operator's message", async () => {
    vi.spyOn(agentApi, "start").mockRejectedValue(
      new ApiError("The LLM client is not configured.", {
        code: "configuration_error",
        status: 503,
      }),
    );
    const user = userEvent.setup();

    render(<AgentConsole />);
    await user.type(screen.getByLabelText("Command for the agent"), "check erp");
    await user.click(screen.getByRole("button", { name: /send/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The agent is not configured",
    );
    expect(screen.getByText("check erp")).toBeInTheDocument();
  });
});
