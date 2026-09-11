import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// jsdom implements neither of these, and the app uses both.
if (!globalThis.EventSource) {
  class MockEventSource {
    close() {}
    addEventListener() {}
    removeEventListener() {}
  }
  // @ts-expect-error - test shim
  globalThis.EventSource = MockEventSource;
}

if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}
