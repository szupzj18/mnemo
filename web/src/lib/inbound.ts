// Inbound links (mnemo link): whether a remote can search this device back.

import type { LinkInfo, LinkState } from "./api"

export type Tone = "ok" | "warn" | "err" | "muted"

const LABEL: Record<LinkState, [string, Tone]> = {
  off: ["关闭", "muted"],
  connecting: ["连接中", "warn"],
  connected: ["已连接", "ok"],
  retrying: ["重连中", "warn"],
  refused: ["被拒绝", "err"],
  stopped: ["未运行", "err"],
}

/** Label and tone for a link, e.g. 已连接 / 重连中 (and why) / 未运行 (installed but not running). */
export function linkLabel(info: LinkInfo | undefined): { text: string; tone: Tone; detail?: string } {
  if (!info) return { text: "—", tone: "muted" }
  const [text, tone] = LABEL[info.state] ?? ["未知", "muted"]
  const detail = info.state !== "connected" && info.error ? info.error : undefined
  return { text, tone, detail }
}

/** Whether a link is on (a service is installed, or a link process is running by hand). */
export function linkOn(info: LinkInfo | undefined): boolean {
  return Boolean(info && (info.installed || (info.state !== "off" && info.state !== "stopped")))
}

/** States worth polling for after switching a link on or off. */
export function settling(info: LinkInfo | undefined, want: boolean): boolean {
  if (!info) return true
  if (!want) return info.state !== "off" && info.state !== "stopped"
  return info.state === "off" || info.state === "connecting" || info.state === "stopped"
}
