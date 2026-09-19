/**
 * The live-run hook.
 *
 * Every assertion here is about *whose* outcome the hook reports. Attributing
 * one run's terminal state to another is what makes the console announce an
 * answer that does not exist.
 */

import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// No EventSource: the polling path carries the same data through the same
// reducer, and is the one a test can drive deterministically.
vi.stubGlobal("EventSource", undefined);

import { agentApi } from "@/lib/api";
import { useRunStream } from "@/lib/hooks/useRunStream";

import { makeState } from "./setup-mocks";

const RUN_ONE = "11111111-1111-1111-1111-111111111111";
const RUN_TWO = "22222222-2222-2222-2222-222222222222";

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(agentApi, "runEvents").mockResolvedValue({
    events: [],
    cursor: null,
    has_more: false,
  });
});

describe("useRunStream", () => {
  it("reports a finished run", async () => {
    vi.spyOn(agentApi, "state").mockResolvedValue(
      makeState({ run_id: RUN_ONE, run_status: "COMPLETED", phase: "COMPLETED" }),
    );

    const { result } = renderHook(() => useRunStream(RUN_ONE));

    await waitFor(() => expect(result.current.finished).toBe(true));
  });

  it("does not carry one run's finished state into the next", async () => {
    vi.spyOn(agentApi, "state").mockImplementation(async (runId) =>
      runId === RUN_ONE
        ? makeState({ run_id: RUN_ONE, run_status: "COMPLETED", phase: "COMPLETED" })
        : makeState({ run_id: RUN_TWO, run_status: "RUNNING", phase: "THINKING", busy: true }),
    );

    const { result, rerender } = renderHook(({ id }) => useRunStream(id), {
      initialProps: { id: RUN_ONE },
    });
    await waitFor(() => expect(result.current.finished).toBe(true));

    rerender({ id: RUN_TWO });

    // Synchronously, in the very commit that first carries the new id: a
    // consumer reading `finished` here would otherwise append the previous
    // run's outcome to the new run's conversation.
    expect(result.current.finished).toBe(false);
    expect(result.current.events).toEqual([]);

    await waitFor(() =>
      expect(result.current.state?.run_status).toBe("RUNNING"),
    );
    expect(result.current.finished).toBe(false);
  });

  it("treats a run paused for approval as unfinished", async () => {
    vi.spyOn(agentApi, "state").mockResolvedValue(
      makeState({
        run_id: RUN_ONE,
        run_status: "WAITING_APPROVAL",
        phase: "WAITING_APPROVAL",
        busy: false,
        pending_approval_id: "33333333-3333-3333-3333-333333333333",
      }),
    );

    const { result } = renderHook(() => useRunStream(RUN_ONE));

    await waitFor(() =>
      expect(result.current.state?.run_status).toBe("WAITING_APPROVAL"),
    );
    // The run has stopped, but it has not finished: an approval resumes it.
    expect(result.current.finished).toBe(false);
  });

  it("clears the stream when the run is closed", async () => {
    vi.spyOn(agentApi, "state").mockResolvedValue(
      makeState({ run_id: RUN_ONE, run_status: "COMPLETED", phase: "COMPLETED" }),
    );

    const { result, rerender } = renderHook(
      ({ id }: { id: string | null }) => useRunStream(id),
      { initialProps: { id: RUN_ONE as string | null } },
    );
    await waitFor(() => expect(result.current.finished).toBe(true));

    rerender({ id: null });

    expect(result.current.finished).toBe(false);
    expect(result.current.state).toBeUndefined();
  });
});
