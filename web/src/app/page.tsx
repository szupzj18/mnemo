"use client"

import Link from "next/link"
import { ArrowRight, Clock, Database, MessageSquare, Server } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { DeviceCard, remoteState } from "@/components/device-card"
import { RemoteDescription } from "@/components/remote-description"
import { fmtTs, srcSummary, totals } from "@/lib/format"
import { useStore } from "@/lib/store"

function Stat({ icon: Icon, label, value, sub }: { icon: typeof Database; label: string; value: React.ReactNode; sub: string }) {
  return (
    <div className="rounded-xl border bg-card px-4 py-3.5" data-testid="stat">
      <div className="flex items-center gap-2.5 text-[12.5px] text-faint">
        <span className="grid size-[30px] place-items-center rounded-lg bg-brand-soft text-brand">
          <Icon className="size-4" />
        </span>
        {label}
      </div>
      <div className="tabular mt-2 mb-0.5 text-[26px] font-bold tracking-tight">{value}</div>
      <div className="text-[11.5px] text-faint">{sub}</div>
    </div>
  )
}

export default function DashboardPage() {
  const { status, ping, rstat, checking, syncLocal, syncOne } = useStore()
  if (!status) return <div className="py-16 text-center text-sm text-faint">正在加载状态…</div>

  const t = totals(status.sources)
  const online = status.remotes.filter((r) => ping[r.name]?.ok).length
  const last = status.last_sync ? new Date(status.last_sync * 1000) : null

  return (
    <div className="flex flex-col gap-4.5">
      <Card className="flex-row flex-wrap items-center gap-x-8 gap-y-3 px-6 py-4">
        <span className="size-2.5 rounded-full bg-ok ring-4 ring-ok-soft" aria-hidden />
        <div>
          <div className="text-xs text-faint">本地服务</div>
          <div className="font-semibold">运行中</div>
        </div>
        <div className="min-w-0">
          <div className="text-xs text-faint">索引库</div>
          <div className="truncate font-mono text-xs font-medium" data-testid="db-path">
            {status.db}
          </div>
        </div>
        <div>
          <div className="text-xs text-faint">上次同步</div>
          <div className="font-medium" suppressHydrationWarning data-testid="last-sync">
            {fmtTs(status.last_sync)}
          </div>
        </div>
        <span className="flex-1" />
        <Button variant="outline" onClick={() => void syncLocal()}>
          增量同步
        </Button>
      </Card>

      <div className="grid grid-cols-2 gap-3.5 lg:grid-cols-4">
        <Stat icon={Database} label="索引会话" value={t.files} sub={`本机 ${Object.keys(status.sources).length} 个 agent`} />
        <Stat icon={MessageSquare} label="索引消息" value={t.msgs.toLocaleString()} sub="跨三源统一索引" />
        <Stat icon={Server} label="远程设备" value={status.remotes.length} sub={`${online} 台在线 / 共 ${status.remotes.length} 台`} />
        <Stat
          icon={Clock}
          label="上次同步"
          value={<span suppressHydrationWarning>{last ? last.toLocaleTimeString() : "—"}</span>}
          sub={last ? last.toLocaleDateString() : "请先同步"}
        />
      </div>

      <Card className="gap-4 px-6 py-5">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold">设备状态</h2>
          <span className="text-[12.5px] text-faint">每台设备的实时连接与索引</span>
          <span className="flex-1" />
          <Button variant="ghost" size="sm" render={<Link href="/devices/" />}>
            管理设备 <ArrowRight />
          </Button>
        </div>
        <div className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-3.5">
          <DeviceCard
            name="local"
            tag="本机"
            local
            state="ok"
            description={`运行中，已索引 ${t.files} 个会话、${t.msgs.toLocaleString()} 条消息`}
            sources={srcSummary(status.sources)}
            lastSync={status.last_sync}
            onSync={() => void syncLocal()}
          />
          {status.remotes.map((r) => {
            const state = remoteState(r.name, ping, checking)
            const stat = rstat[r.name]
            return (
              <DeviceCard
                key={r.name}
                name={r.name}
                tag={r.host}
                state={state}
                description={<RemoteDescription state={state} ping={ping[r.name]} stat={stat} />}
                sources={stat && state === "ok" ? srcSummary(stat.sources) : undefined}
                lastSync={stat ? stat.last_sync : undefined}
                onSync={() => void syncOne(r.name)}
              />
            )
          })}
          {!status.remotes.length ? (
            <div className="col-span-full py-10 text-center text-[13px] text-faint">
              还没有远程设备，到「设备管理」添加。
            </div>
          ) : null}
        </div>
      </Card>
    </div>
  )
}
