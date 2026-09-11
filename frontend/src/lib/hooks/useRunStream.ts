"use client";

/**
 * Live timeline for one agent run.
 *
 * Server-Sent Events are the transport, with polling as the fallback when the
 * browser cannot hold the stream open. Both paths converge on the same reducer,
 * so the rest of the UI never learns which one is in use — and swapping in a
 * WebSocket later touches only this file.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { agentApi, type AgentEvent, type AgentStateSnapshot } from "@/lib/api";

export type StreamTransport = "sse" | "polling" | "idle";

export interface RunStream {
  events: AgentEvent[];
  state: AgentStateSnapshot | undefined;
  transport: StreamTransport;
  finished: boolean;
  error: string | undefined;
}

const POLL_INTERVAL_MS = 1_200;

function mergeEvent(events: AgentEvent[], incoming: AgentEvent): AgentEvent[] {
  // The stream can redeliver after a reconnect; sequence is the identity.
  if (events.some((event) => event.sequence === incoming.sequence)) return events;
  return [...events, incoming].sort((a, b) => a.sequence - b.sequence);
}

export function useRunStream(runId: string | null | undefined): RunStream {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [state, setState] = useState<AgentStateSnapshot | undefined>(undefined);
  const [transport, setTransport] = useState<StreamTransport>("idle");
  const [finished, setFinished] = useState(false);
  const [error, setError] = useState<string | undefined>(undefined);

  const cursorRef = useRef(0);

  const reset = useCallback(() => {
    cursorRef.current = 0;
    setEvents([]);
    setState(undefined);
    setFinished(false);
    setError(undefined);
  }, []);

  useEffect(() => {
    reset();
    if (!runId) {
      setTransport("idle");
      return;
    }

    let closed = false;
    let source: EventSource | undefined;
    let pollTimer: ReturnType<typeof setInterval> | undefined;

    const applyEvent = (event: AgentEvent) => {
      cursorRef.current = Math.max(cursorRef.current, event.sequence);
      setEvents((current) => mergeEvent(current, event));
    };

    /** Fallback path: same data, polled. */
    const startPolling = () => {
      if (closed || pollTimer) return;
      setTransport("polling");
      const tick = async () => {
        try {
          const page = await agentApi.runEvents(runId, cursorRef.current);
          page.events.forEach(applyEvent);
          const snapshot = await agentApi.state(runId);
          setState(snapshot);
          if (!snapshot.busy && snapshot.run_status !== "RUNNING") {
            setFinished(true);
            if (pollTimer) clearInterval(pollTimer);
          }
        } catch {
          setError("Lost connection to the agent stream.");
        }
      };
      void tick();
      pollTimer = setInterval(tick, POLL_INTERVAL_MS);
    };

    if (typeof EventSource === "undefined") {
      startPolling();
      return () => {
        closed = true;
        if (pollTimer) clearInterval(pollTimer);
      };
    }

    source = new EventSource(agentApi.streamUrl(runId, 0));
    setTransport("sse");

    source.addEventListener("agent-event", (message) => {
      try {
        applyEvent(JSON.parse((message as MessageEvent).data) as AgentEvent);
      } catch {
        /* a malformed frame must not break the timeline */
      }
    });

    source.addEventListener("agent-state", (message) => {
      try {
        setState(JSON.parse((message as MessageEvent).data) as AgentStateSnapshot);
      } catch {
        /* ignore */
      }
    });

    source.addEventListener("done", () => {
      setFinished(true);
      source?.close();
    });

    source.addEventListener("error", () => {
      // EventSource retries on its own; if it has given up, fall back.
      if (source?.readyState === EventSource.CLOSED && !closed) {
        startPolling();
      }
    });

    return () => {
      closed = true;
      source?.close();
      if (pollTimer) clearInterval(pollTimer);
    };
  }, [runId, reset]);

  return { events, state, transport, finished, error };
}
