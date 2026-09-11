/** The API client is the only place errors are translated — so it is tested hard. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, buildQuery, request } from "@/lib/api/client";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
});

afterEach(() => vi.unstubAllGlobals());

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "",
    json: async () => body,
  } as Response;
}

/** Assert that a request rejects, and hand back the typed error. */
async function expectFailure(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (caught) {
    return caught as ApiError;
  }
  throw new Error("expected the request to fail, but it resolved");
}

describe("buildQuery", () => {
  it("drops empty values so blank filters never reach the backend", () => {
    expect(buildQuery({ status: "", limit: 10, project: undefined })).toBe(
      "?limit=10",
    );
  });

  it("repeats a key for array values", () => {
    expect(buildQuery({ type: ["plan", "replan"] })).toBe(
      "?type=plan&type=replan",
    );
  });

  it("returns an empty string when there is nothing to send", () => {
    expect(buildQuery()).toBe("");
    expect(buildQuery({})).toBe("");
  });
});

describe("request", () => {
  it("returns the parsed body on success", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }));
    await expect(request("/health")).resolves.toEqual({ ok: true });
  });

  it("translates the backend error envelope into a typed ApiError", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        {
          error: {
            code: "not_found",
            message: "Project abc not found.",
            details: { project_id: "abc" },
          },
        },
        404,
      ),
    );

    const error = await expectFailure(request("/projects/abc"));

    expect(error).toBeInstanceOf(ApiError);
    expect(error.code).toBe("not_found");
    expect(error.status).toBe(404);
    expect(error.details).toEqual({ project_id: "abc" });
    expect(error.isRetryable).toBe(false);
  });

  it("flags a missing API key as a configuration problem", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        { error: { code: "configuration_error", message: "no key" } },
        503,
      ),
    );

    const error = await expectFailure(request("/agent/run"));

    expect(error.isConfiguration).toBe(true);
    expect(error.isRetryable).toBe(true);
  });

  it("handles FastAPI validation errors, which do not use the envelope", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ detail: [{ loc: ["body", "message"] }] }, 422),
    );

    const error = await expectFailure(request("/agent/run"));

    expect(error.code).toBe("validation_error");
    expect(error.message).not.toContain("loc");
  });

  it("reports an unreachable backend without leaking the raw failure", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    const error = await expectFailure(request("/health"));

    expect(error.code).toBe("network_error");
    expect(error.message).toContain("Could not reach");
  });

  it("returns undefined for a 204 instead of trying to parse a body", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 204,
      json: async () => {
        throw new Error("must not be called");
      },
    } as unknown as Response);

    await expect(request("/memory/x", { method: "DELETE" })).resolves.toBeUndefined();
  });

  it("sends a JSON body with the right header", async () => {
    fetchMock.mockResolvedValue(jsonResponse({}));

    await request("/agent/runs", { method: "POST", body: { message: "hi" } });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit & {
      headers: Record<string, string>;
      body: string;
    }];
    expect(init.method).toBe("POST");
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body)).toEqual({ message: "hi" });
  });
});
