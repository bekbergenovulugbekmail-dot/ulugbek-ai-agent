/** The approval flow: the one place a human gates the agent. */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApprovalCard } from "@/components/cards/ApprovalCard";
import { ApiError, approvalApi } from "@/lib/api";

import { makeApproval } from "./setup-mocks";

describe("ApprovalCard", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("shows what is being asked, at what risk level", () => {
    render(<ApprovalCard approval={makeApproval()} />);

    expect(screen.getByText("Waiting for your approval")).toBeInTheDocument();
    expect(screen.getByText("Deploy")).toBeInTheDocument();
    expect(screen.getByText("CRITICAL")).toBeInTheDocument();
    expect(
      screen.getByText(/CRITICAL actions require explicit human approval/),
    ).toBeInTheDocument();
  });

  it("never renders a raw credential — the backend redacted it first", () => {
    render(
      <ApprovalCard
        approval={makeApproval({
          tool_arguments: {
            environment: "production",
            api_key: "***REDACTED***",
          },
        })}
      />,
    );

    expect(screen.getByText("***REDACTED***")).toBeInTheDocument();
    expect(screen.queryByText(/sk-ant/)).not.toBeInTheDocument();
  });

  it("approves through the API and reports the decision upwards", async () => {
    const approve = vi
      .spyOn(approvalApi, "approve")
      .mockResolvedValue({} as never);
    const onDecided = vi.fn();
    const user = userEvent.setup();
    const approval = makeApproval();

    render(<ApprovalCard approval={approval} onDecided={onDecided} />);
    await user.click(screen.getByRole("button", { name: /approve/i }));

    await waitFor(() => expect(onDecided).toHaveBeenCalledWith(approval.id, true));
    expect(approve).toHaveBeenCalledWith(approval.id);
  });

  it("rejects without killing the run", async () => {
    const reject = vi
      .spyOn(approvalApi, "reject")
      .mockResolvedValue({} as never);
    const onDecided = vi.fn();
    const user = userEvent.setup();
    const approval = makeApproval();

    render(<ApprovalCard approval={approval} onDecided={onDecided} />);
    await user.click(screen.getByRole("button", { name: /reject/i }));

    await waitFor(() => expect(onDecided).toHaveBeenCalledWith(approval.id, false));
    expect(reject).toHaveBeenCalledWith(approval.id);
  });

  it("surfaces a failed decision instead of silently doing nothing", async () => {
    vi.spyOn(approvalApi, "approve").mockRejectedValue(
      new ApiError("Approval was already decided.", {
        code: "conflict",
        status: 409,
      }),
    );
    const onDecided = vi.fn();
    const user = userEvent.setup();

    render(<ApprovalCard approval={makeApproval()} onDecided={onDecided} />);
    await user.click(screen.getByRole("button", { name: /approve/i }));

    expect(
      await screen.findByText("Approval was already decided."),
    ).toBeInTheDocument();
    expect(onDecided).not.toHaveBeenCalled();
  });

  it("offers no buttons once a decision has been made", () => {
    render(
      <ApprovalCard
        approval={makeApproval({ status: "APPROVED", decided_by: "ulugbek" })}
      />,
    );

    expect(screen.queryByRole("button", { name: /approve/i })).toBeNull();
    expect(screen.getByText("Decided by ulugbek")).toBeInTheDocument();
  });
});
