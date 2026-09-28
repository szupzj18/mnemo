import type { Hit } from "./api"

/** Deep link to the session view, anchored at the hit's line with its search terms. */
export function sessionHref(h: Pick<Hit, "path" | "host" | "lineno">, terms: string[]): string {
  const q = new URLSearchParams({ path: h.path, host: h.host || "local", line: String(h.lineno || "") })
  if (terms.length) q.set("q", terms.join(" "))
  return "/session/?" + q.toString()
}
