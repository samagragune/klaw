"use client";

/**
 * Live update channel.
 *
 * One WebSocket per browser tab, shared through context. Every page that
 * wants live data subscribes to the message stream rather than polling, so
 * what the dashboard shows is what the backend actually broadcast - no
 * simulated ticking.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { getApiUrl, getToken } from "./api";

export type LiveMessage =
  | { type: "connected"; user_id: string }
  | { type: "decision.created"; execution_id: string; decision: Record<string, unknown>; approval_id: string | null }
  | { type: "approval.updated"; approval_id: string; status: string }
  | { type: "heartbeat" }
  | { type: "error"; message: string }
  | { type: "execution.started"; execution: Record<string, unknown> }
  | {
      type: "execution.updated";
      execution: Record<string, unknown>;
      events: Array<Record<string, unknown>>;
    }
  | { type: "incident.updated"; incident: Record<string, unknown> }
  | { type: "intervention.created"; execution_id: string; intervention: Record<string, unknown> }
  | { type: "intervention.updated"; execution_id: string; intervention: Record<string, unknown> };

type Status = "connecting" | "open" | "closed" | "unauthorized";

interface LiveContextValue {
  status: Status;
  lastMessageAt: number | null;
  subscribe: (handler: (message: LiveMessage) => void) => () => void;
}

const LiveContext = createContext<LiveContextValue>({
  status: "closed",
  lastMessageAt: null,
  subscribe: () => () => {},
});

export function LiveProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>("connecting");
  const [lastMessageAt, setLastMessageAt] = useState<number | null>(null);
  const handlers = useRef(new Set<(message: LiveMessage) => void>());
  const socket = useRef<WebSocket | null>(null);
  const retry = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attempts = useRef(0);

  const subscribe = useCallback((handler: (message: LiveMessage) => void) => {
    handlers.current.add(handler);
    return () => {
      handlers.current.delete(handler);
    };
  }, []);

  useEffect(() => {
    let closed = false;

    const connect = () => {
      const token = getToken();
      if (!token) {
        setStatus("unauthorized");
        return;
      }
      const url = `${getApiUrl().replace(/^http/, "ws")}/v1/ws?token=${encodeURIComponent(token)}`;
      const ws = new WebSocket(url);
      socket.current = ws;
      setStatus("connecting");

      ws.onopen = () => {
        attempts.current = 0;
        setStatus("open");
        // A periodic ping keeps intermediaries from idling the socket out.
        const ping = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) ws.send("ping");
        }, 20000);
        ws.addEventListener("close", () => clearInterval(ping));
      };

      ws.onmessage = (event) => {
        let message: LiveMessage;
        try {
          message = JSON.parse(event.data);
        } catch {
          return;
        }
        if (message.type === "error") {
          setStatus("unauthorized");
          return;
        }
        setLastMessageAt(Date.now());
        if (message.type === "heartbeat") return;
        handlers.current.forEach((handler) => handler(message));
      };

      ws.onclose = () => {
        if (closed) return;
        setStatus((current) => (current === "unauthorized" ? current : "closed"));
        // Exponential backoff, capped: a backend restart reconnects on its own.
        const delay = Math.min(1000 * 2 ** attempts.current, 15000);
        attempts.current += 1;
        retry.current = setTimeout(connect, delay);
      };

      ws.onerror = () => ws.close();
    };

    connect();
    return () => {
      closed = true;
      if (retry.current) clearTimeout(retry.current);
      socket.current?.close();
    };
  }, []);

  return (
    <LiveContext.Provider value={{ status, lastMessageAt, subscribe }}>
      {children}
    </LiveContext.Provider>
  );
}

export const useLive = () => useContext(LiveContext);

/** Subscribe to live messages for the lifetime of a component. */
export function useLiveMessages(handler: (message: LiveMessage) => void) {
  const { subscribe } = useLive();
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => subscribe((message) => ref.current(message)), [subscribe]);
}
