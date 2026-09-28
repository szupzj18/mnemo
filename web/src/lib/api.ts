// Typed client for the Python dashboard API (mnemo/dashboard.py).

export type SourceCounts = Record<string, { files: number; messages: number }>

export interface Remote {
  name: string
  host: string
  bin: string
}

export interface Status {
  db: string
  last_sync: number | null
  sources: SourceCounts
  remotes: Remote[]
  error?: string
}

export interface RemoteStatus {
  name: string
  ms: number
  db: string
  last_sync: number | null
  sources: SourceCounts
}

export interface Ping {
  name: string
  ok: boolean
  ms?: number
  error?: string
}

export interface Hit {
  host: string
  source: string
  session_id: string
  cwd: string
  ts: string
  role: string
  kind: string
  snippet: string
  path: string
  lineno: number
  rank: number
}

export interface HostResult {
  host: string
  ok: boolean
  ms?: number
  hits?: number
  error?: string
}

export interface SearchResponse {
  per_host: HostResult[]
  merged: Hit[]
  warnings: string[]
  error?: string
}

export interface Message {
  lineno: number
  ts: string
  role: string
  kind: string
  text: string
}

export interface Session {
  path: string
  source: string
  session_id: string
  cwd: string
  started_at: string | null
  ended_at: string | null
  count: number
  messages: Message[]
}

export interface SyncStats {
  files_new: number
  files_updated: number
  files_removed: number
  messages: number
}

const TOKEN_PLACEHOLDER = "__MNEMO_TOKEN__"

/** The Python server injects a per-launch token into this meta tag; `next dev` uses an env var. */
export function dashboardToken(): string {
  if (typeof document === "undefined") return ""
  const meta = document.querySelector<HTMLMetaElement>('meta[name="mnemo-token"]')
  const value = meta?.content ?? ""
  if (value && value !== TOKEN_PLACEHOLDER) return value
  return process.env.NEXT_PUBLIC_MNEMO_TOKEN ?? ""
}

export class ApiError extends Error {}

async function call<T>(path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "X-Dashboard-Token": dashboardToken() }
  const init: RequestInit =
    body === undefined
      ? { headers }
      : { method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify(body) }
  const res = await fetch(path, init)
  let data: unknown
  try {
    data = await res.json()
  } catch {
    throw new ApiError(`HTTP ${res.status}`)
  }
  return data as T
}

type Ok<T> = ({ ok: true } & T) | { ok: false; error: string }

export const api = {
  status: () => call<Status>("/api/status"),
  remoteStatus: (name: string) =>
    call<Ok<{ status: RemoteStatus }>>("/api/remote-status?name=" + encodeURIComponent(name)),
  ping: (name?: string) => call<{ results: Ping[] }>("/api/ping", name ? { name } : {}),
  syncLocal: () => call<Ok<{ ms: number; stats: SyncStats }>>("/api/sync", {}),
  syncRemote: (name: string) => call<Ok<{ name: string; ms: number; output: string }>>("/api/sync", { name }),
  addRemote: (name: string, host: string, bin: string) =>
    call<Ok<{ logs: string[] }>>("/api/remotes/add", { name, host, bin }),
  removeRemote: (name: string) => call<Ok<object>>("/api/remotes/remove", { name }),
  updateRemotes: (name?: string) => call<Ok<{ logs: string[] }>>("/api/remotes/update", name ? { name } : {}),
  search: (query: string, limit: number, hosts: string[] | null) =>
    call<SearchResponse>("/api/search", { query, limit, hosts }),
  session: (path: string, host: string, raw: boolean) =>
    call<Ok<{ session: Session }>>("/api/session", { path, host, raw }),
}
