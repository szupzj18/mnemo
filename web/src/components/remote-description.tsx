"use client"

import type { Ping, RemoteStatus } from "@/lib/api"
import { totals } from "@/lib/format"
import type { DeviceState } from "./device-card"

export function RemoteDescription({ state, ping, stat }: { state: DeviceState; ping?: Ping; stat?: RemoteStatus }) {
  if (state === "checking") return <>正在连接…</>
  if (state === "unknown") return <>尚未检测连接</>
  if (state === "bad") {
    const e = String(ping?.error || "未知错误").split("\n")[0]
    return (
      <span className="line-clamp-2 break-all" title={e}>
        无法连接：{e}
      </span>
    )
  }
  return (
    <>
      在线，延迟 {ping?.ms} ms{stat ? `，已索引 ${totals(stat.sources).files} 个会话` : ""}
    </>
  )
}
