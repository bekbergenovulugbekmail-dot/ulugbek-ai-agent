/** Presentational components: timeline, command input, cards and error states. */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ActivityTimeline } from "@/components/agent/ActivityTimeline";
import { AgentStatus } from "@/components/agent/AgentStatus";
import { CommandInput } from "@/components/agent/CommandInput";
import { ProgressIndicator } from "@/components/agent/ProgressIndicator";
import { ToolExecutionCard } from "@/components/cards/ToolExecutionCard";
import { ErrorState } from "@/components/ui/States";
import { ApiError } from "@/lib/api";

import { makeEvent, makeState } from "./setup-mocks";

describe("ActivityTimeline", () => {
  it("renders each event with its safe message", () => {
    render(
      <ActivityTimeline
        events={[
          makeEvent({ sequence: 1, type: "agent.started", safe_message: "Request received" }),
          makeEvent({ sequence: 2, type: "tool.started", safe_message: "Tool requested: calculate" }),
          makeEvent({ sequence: 3, type: "task.completed", safe_message: "Run completed" }),
        ]}
      />,
    );

    expect(screen.getAllByTestId("timeline-event")).toHaveLength(3);
    expect(screen.getByText("Request received")).toBeInTheDocument();
    expect(screen.getByText("Run completed")).toBeInTheDocument();
  });

  it("shows a failure reason when the backend supplied one", () => {
    render(
      <ActivityTimeline
        events={[
          makeEvent({
            type: "tool.failed",
            status: "failure",
            safe_message: "Tool deploy: failed",
            metadata: { error: "GitHub API unavailable" },
          }),
        ]}
      />,
    );

    expect(screen.getByText("GitHub API unavailable")).toBeInTheDocument();
  });

  it("shows an empty state rather than a blank panel", () => {
    render(<ActivityTimeline events={[]} />);
    expect(screen.getByText("No activity yet")).toBeInTheDocument();
  });
});

describe("AgentStatus", () => {
  it("names the concrete phase instead of a generic spinner", () => {
    render(
      <AgentStatus
        state={makeState({
          phase: "USING_TOOL",
          label: "Using a tool",
          busy: true,
          detail: "Tool requested: calculate",
        })}
      />,
    );

    expect(screen.getByTestId("agent-phase")).toHaveTextContent("USING TOOL");
    expect(screen.getByText("Using a tool")).toBeInTheDocument();
    expect(screen.getByText("Tool requested: calculate")).toBeInTheDocument();
  });

  it("falls back to idle when there is no state yet", () => {
    render(<AgentStatus state={undefined} />);
    expect(screen.getByTestId("agent-phase")).toHaveTextContent("IDLE");
  });
});

describe("CommandInput", () => {
  it("submits the typed message and clears the field", async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(<CommandInput onSubmit={onSubmit} />);

    const box = screen.getByLabelText("Command for the agent");
    await user.type(box, "Telegram loyihamni tekshir");
    await user.click(screen.getByRole("button", { name: /send/i }));

    expect(onSubmit).toHaveBeenCalledWith("Telegram loyihamni tekshir");
    expect(box).toHaveValue("");
  });

  it("sends on Enter but not on Shift+Enter", async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(<CommandInput onSubmit={onSubmit} />);

    const box = screen.getByLabelText("Command for the agent");
    await user.type(box, "first line{Shift>}{Enter}{/Shift}second line");
    expect(onSubmit).not.toHaveBeenCalled();

    await user.type(box, "{Enter}");
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0][0]).toContain("first line");
  });

  it("refuses to send whitespace", async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(<CommandInput onSubmit={onSubmit} />);

    await user.type(screen.getByLabelText("Command for the agent"), "   ");
    expect(screen.getByRole("button", { name: /send/i })).toBeDisabled();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("locks the input while the agent is working", () => {
    render(<CommandInput onSubmit={vi.fn()} busy />);

    expect(screen.getByLabelText("Command for the agent")).toBeDisabled();
    expect(screen.getByRole("button", { name: /working/i })).toBeDisabled();
  });
});

describe("ProgressIndicator", () => {
  it("reports real step progress, not invented precision", () => {
    render(<ProgressIndicator current={2} total={4} />);

    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "50");
    expect(screen.getByText("2/4")).toBeInTheDocument();
  });

  it("survives an empty plan without dividing by zero", () => {
    render(<ProgressIndicator current={0} total={0} />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "0");
  });
});

describe("ToolExecutionCard", () => {
  it("summarises a run without dumping the payload", () => {
    render(
      <ToolExecutionCard
        execution={{
          id: "e1",
          tool_name: "github_commit",
          status: "SUCCESS",
          permission: "EXECUTE",
          arguments: { repository: "ulugbek/erp" },
          output: { sha: "abc123" },
          error: null,
          duration_ms: 2400,
          verification_status: "SUCCESS",
          verification_reason: "commit present",
          agent_run_id: null,
          task_id: null,
          created_at: new Date().toISOString(),
        }}
      />,
    );

    expect(screen.getByText("Github Commit")).toBeInTheDocument();
    expect(screen.getByText("2.4s")).toBeInTheDocument();
    expect(screen.getByText("EXECUTE")).toBeInTheDocument();
    // The raw output must not be rendered.
    expect(screen.queryByText(/abc123/)).not.toBeInTheDocument();
  });

  it("shows the reason when a tool failed", () => {
    render(
      <ToolExecutionCard
        execution={{
          id: "e2",
          tool_name: "deploy",
          status: "FAILED",
          permission: "CRITICAL",
          arguments: {},
          output: null,
          error: "GitHub API unavailable",
          duration_ms: 900,
          verification_status: null,
          verification_reason: null,
          agent_run_id: null,
          task_id: null,
          created_at: new Date().toISOString(),
        }}
      />,
    );

    expect(screen.getByText("GitHub API unavailable")).toBeInTheDocument();
    expect(screen.getByText("FAILED")).toBeInTheDocument();
  });
});

describe("ErrorState", () => {
  it("explains a missing API key in terms the operator can act on", () => {
    render(
      <ErrorState
        error={
          new ApiError("The LLM client is not configured.", {
            code: "configuration_error",
            status: 503,
          })
        }
      />,
    );

    expect(screen.getByText("The agent is not configured")).toBeInTheDocument();
    expect(screen.getByText(/ANTHROPIC_API_KEY/)).toBeInTheDocument();
  });

  it("offers a retry and calls it", async () => {
    const onRetry = vi.fn();
    const user = userEvent.setup();
    render(
      <ErrorState
        error={new ApiError("Could not reach the ULUGBEK AI backend.", {
          code: "network_error",
        })}
        onRetry={onRetry}
      />,
    );

    await user.click(screen.getByRole("button", { name: /retry/i }));
    expect(onRetry).toHaveBeenCalled();
  });
});
