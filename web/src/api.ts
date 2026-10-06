import { useCallback, useEffect, useRef, useState } from "react";

export type Source = "bedrock" | "stub" | "rules";

export interface Clock {
  sim_now: string;
  real_now: string;
  offset_s: number;
  tz: string;
}

export interface DoorEvent {
  event_id: string;
  event_type: string;
  real_ts: string;
  sim_ts: string;
  sim_hour: number;
  source: string;
  frame_source: string | null;
  frame_count: number;
  package_seen: boolean;
  vehicle_seen: boolean;
  person_seen: boolean;
  description_source: "bedrock" | "stub" | null;
  accessible_description: string | null;
  unusual_score: number | null;
  unusual_explanation: string | null;
  package_check: string | null;
  snapshot_url: string | null;
  agent_brain: "bedrock" | "rules" | null;
  agent_reason: string | null;
  agent_trace: TraceEntry[] | null;
}

export interface TraceEntry {
  tool: string;
  brain?: string;
  ok?: boolean;
  input?: Record<string, unknown>;
  output?: string;
  error?: string;
  sim_ts?: string;
  ms?: number;
}

export interface Package {
  id: string;
  status: "present" | "reminded" | "picked_up" | "missing";
  arrived_event_id: string;
  arrived_sim_ts: string;
  last_seen_sim_ts: string;
  reminded_sim_ts: string | null;
  resolved_sim_ts: string | null;
}

export interface Notification {
  id: string;
  audience: "resident" | "caregiver";
  kind: string;
  text: string;
  event_id: string | null;
  package_id: string | null;
  source: Source;
  sim_ts: string;
  status: string;
  extra?: { observation_source?: "bedrock" | "stub" | null; score?: number; [k: string]: unknown };
}

/** True when the underlying scene description was the detector-only stand-in. */
export const isEstimate = (n: { source: string; extra?: { observation_source?: string | null } }) =>
  n.source === "stub" || n.extra?.observation_source === "stub";

export interface State {
  agent?: { brain: "bedrock" | "rules"; why: string };
  clock: Clock;
  packages: Package[];
  notifications: Notification[];
  events: DoorEvent[];
}

export const POLL_MS = 3000;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body?.detail ?? body?.error ?? `${res.status} ${res.statusText}`);
  }
  return body as T;
}

export const api = {
  state: () => request<State>("/state"),
  pickedUp: (id: string) => request<Package>(`/packages/${encodeURIComponent(id)}/picked-up`, { method: "POST" }),
  clock: (body: { set?: string; advance_hours?: number; reset?: boolean }) =>
    request<Clock & { reminders_queued: Notification[] }>("/demo/clock", { method: "POST", body: JSON.stringify(body) }),
  digest: () =>
    request<{ brain: string; reason: string; notification: Notification | null }>("/digest", {
      method: "POST",
      body: JSON.stringify({}),
    }),
  replay: (event_type: "package" | "vehicle" | "motion") =>
    request<{ event_id: string; capture: string; reason: string }>("/demo/replay", {
      method: "POST",
      body: JSON.stringify({ event_type }),
    }),
  simulate: (event_type: "package" | "vehicle" | "motion") =>
    request<{ status: string; event_id: string; presenter_hint: string }>("/simulate-event", {
      method: "POST",
      body: JSON.stringify({ event_type }),
    }),
};

/** Fetch /state now and every POLL_MS; `refresh()` forces an immediate reload. */
export function useDoorState() {
  const [state, setState] = useState<State | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);

  const refresh = useCallback(async () => {
    try {
      setState(await api.state());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    refresh();
    timer.current = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(timer.current);
  }, [refresh]);

  return { state, error, refresh };
}

export function formatTime(iso: string, tz: string): string {
  return new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: tz });
}

export function formatDateTime(iso: string, tz: string): string {
  return new Date(iso).toLocaleString("en-US", {
    weekday: "short", hour: "numeric", minute: "2-digit", timeZone: tz,
  });
}

export const newestFirst = <T extends { sim_ts: string }>(items: T[]): T[] =>
  [...items].sort((a, b) => Date.parse(b.sim_ts) - Date.parse(a.sim_ts));

export const openPackage = (packages: Package[]): Package | undefined =>
  [...packages].reverse().find((p) => p.status === "present" || p.status === "reminded");
