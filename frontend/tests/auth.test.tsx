/**
 * The console's half of authentication.
 *
 * The properties worth pinning are the ones that fail quietly: a request that
 * forgets the header looks like a server problem, and a rejected token looks
 * like nine unrelated broken panels.
 */

import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OperatorGate } from "@/components/auth/OperatorGate";
import {
  ApiError,
  request,
  setAuthTokenProvider,
  setUnauthorizedHandler,
} from "@/lib/api";
import { installOperatorToken, operatorToken, setOperatorToken } from "@/lib/auth";

const TOKEN = "test-operator-token-not-a-real-credential";

beforeEach(() => {
  window.localStorage.clear();
  setOperatorToken(null);
  setAuthTokenProvider(() => null);
  setUnauthorizedHandler(() => {});
  vi.restoreAllMocks();
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("the operator gate", () => {
  it("asks for a token before showing anything", () => {
    render(
      <OperatorGate>
        <p>the console</p>
      </OperatorGate>,
    );

    expect(screen.getByLabelText("Operator token")).toBeInTheDocument();
    expect(screen.queryByText("the console")).not.toBeInTheDocument();
  });

  it("does not put the token in a readable field", () => {
    render(
      <OperatorGate>
        <p>the console</p>
      </OperatorGate>,
    );

    // Shoulder-reading a shared operator secret is the cheapest attack there is.
    expect(screen.getByLabelText("Operator token")).toHaveAttribute(
      "type",
      "password",
    );
  });

  it("opens once a token is entered, and remembers it", async () => {
    const user = userEvent.setup();
    render(
      <OperatorGate>
        <p>the console</p>
      </OperatorGate>,
    );

    await user.type(screen.getByLabelText("Operator token"), TOKEN);
    await user.click(screen.getByRole("button", { name: /continue/i }));

    expect(await screen.findByText("the console")).toBeInTheDocument();
    expect(operatorToken()).toBe(TOKEN);
  });

  it("shows the console straight away when a token is already stored", async () => {
    setOperatorToken(TOKEN);

    render(
      <OperatorGate>
        <p>the console</p>
      </OperatorGate>,
    );

    expect(await screen.findByText("the console")).toBeInTheDocument();
    expect(screen.queryByLabelText("Operator token")).not.toBeInTheDocument();
  });

  it("asks again, and says why, when the server rejects the token", async () => {
    setOperatorToken(TOKEN);
    render(
      <OperatorGate>
        <p>the console</p>
      </OperatorGate>,
    );
    await screen.findByText("the console");

    // What a 401 does, wired the way the app wires it.
    installOperatorToken();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ error: { code: "authentication_required" } }, 401),
    );
    // The 401 handler clears the token, which re-renders the gate: `act` so the
    // assertions below see the settled tree rather than a warning.
    await act(async () => {
      await expect(request("/projects")).rejects.toBeInstanceOf(ApiError);
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(/refused/i);
    expect(screen.queryByText("the console")).not.toBeInTheDocument();
    expect(operatorToken()).toBeNull();
  });
});

describe("the request credential", () => {
  it("is attached to every request", async () => {
    setOperatorToken(TOKEN);
    installOperatorToken();
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse([]));

    await request("/projects");

    const headers = (fetchMock.mock.calls[0]?.[1]?.headers ?? {}) as Record<
      string,
      string
    >;
    expect(headers.Authorization).toBe(`Bearer ${TOKEN}`);
  });

  it("is absent when there is none, rather than sent empty", async () => {
    setAuthTokenProvider(() => null);
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse([]));

    await request("/projects");

    const headers = (fetchMock.mock.calls[0]?.[1]?.headers ?? {}) as Record<
      string,
      string
    >;
    expect(headers.Authorization).toBeUndefined();
  });

  it("never appears in the error a component would display", async () => {
    setOperatorToken(TOKEN);
    installOperatorToken();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ error: { code: "internal_error", message: "boom" } }, 500),
    );

    const error = (await request("/projects").catch(
      (reason: unknown) => reason,
    )) as ApiError;

    expect(error).toBeInstanceOf(ApiError);
    // Not only the message: anything a panel might render or a log might carry.
    expect(error.message).not.toContain(TOKEN);
    expect(JSON.stringify(error.details)).not.toContain(TOKEN);
  });
});
