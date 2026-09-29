"use client"

import * as React from "react"
import Link from "next/link"
import { Loader2, RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { TopologyGraph } from "@/components/topology-graph"
import { useStore } from "@/lib/store"
import { buildGraph, type GraphNode } from "@/lib/topology"
import { cn } from "@/lib/utils"

const STATE: Record<GraphNode["state"], [string, string]> = {
  ok: ["在线", "bg-ok-soft text-ok"],
  bad: ["离线", "bg-err-soft text-err"],
  unknown: ["未探测", "bg-muted text-faint"],
}

function Legend() {
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 text-xs text-faint">
      <span className="flex items-center gap-1.5">
        <span className="h-0.5 w-5 bg-ok/70" /> 连通
      </span>
      <span className="flex items-center gap-1.5">
        <span className="w-5 border-t-2 border-dashed border-err/70" /> 连接失败
      </span>
      <span className="flex items-center gap-1.5">
        <span className="w-5 border-t-2 border-dotted border-faint" /> 未探测（超出跳数，或已由更短路径覆盖）
      </span>
      <span className="flex items-center gap-1.5">
        <span className="rounded-full bg-brand-soft px-1.5 text-[10px] text-brand">中转</span> 允许邻居经由它访问后方设备
      </span>
    </div>
  )
}

export default function TopologyPage() {
  const { status, topology, topologyLoading, probeTopology } = useStore()

  React.useEffect(() => {
    if (!topology && !topologyLoading) void probeTopology()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const graph = React.useMemo(() => (topology ? buildGraph(topology.topology) : null), [topology])
  const remotes = status?.remotes.length ?? 0
  const reachable = graph ? graph.nodes.filter((n) => n.depth > 0 && n.state === "ok").length : 0

  return (
    <div className="flex flex-col gap-4.5">
      <Card className="gap-4 px-6 py-5">
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex flex-col gap-0.5">
            <h2 className="text-sm font-semibold">从本机可达的设备</h2>
            <p className="text-xs text-faint" data-testid="topology-summary">
              {graph
                ? `${reachable} 台在线 · 共 ${graph.nodes.length - 1} 台 · 最多 ${topology!.ttl} 跳 · 探测用时 ${topology!.ms} ms`
                : "与搜索走同样的路径：只经过开启中转的设备，最多 3 跳。"}
            </p>
          </div>
          <span className="flex-1" />
          <Button variant="outline" size="sm" disabled={topologyLoading} onClick={() => void probeTopology()}>
            {topologyLoading ? <Loader2 className="animate-spin" /> : <RefreshCw />}
            {topologyLoading ? "探测中" : "重新探测"}
          </Button>
        </div>
        {graph ? (
          <>
            <TopologyGraph graph={graph} />
            <Legend />
          </>
        ) : (
          <div className="py-16 text-center text-[13px] text-faint">
            {topologyLoading ? "正在探测设备网络…" : "尚未探测"}
          </div>
        )}
        {graph && !remotes ? (
          <p className="text-center text-[13px] text-faint">
            尚未注册远程设备，前往
            <Link className="mx-1 text-brand hover:underline" href="/devices/">
              设备管理
            </Link>
            添加。
          </p>
        ) : null}
      </Card>

      {graph && graph.nodes.length > 1 ? (
        <Card className="gap-0 overflow-hidden p-0">
          <table className="w-full text-left text-[13px]" data-testid="topology-table">
            <thead className="border-b bg-muted/50 text-xs text-faint">
              <tr>
                <th className="px-5 py-2.5 font-medium">路由（搜索结果中的设备名）</th>
                <th className="px-3 py-2.5 font-medium">节点名</th>
                <th className="px-3 py-2.5 font-medium">状态</th>
                <th className="px-3 py-2.5 font-medium">中转</th>
                <th className="px-5 py-2.5 text-right font-medium">延迟</th>
              </tr>
            </thead>
            <tbody>
              {graph.nodes.slice(1).map((n) => (
                <tr key={n.key} className="border-b last:border-0" data-testid="topology-row" data-route={n.route}>
                  <td className="px-5 py-2.5 font-mono text-xs">{n.route}</td>
                  <td className="px-3 py-2.5">{n.nodeName ?? "—"}</td>
                  <td className="px-3 py-2.5">
                    <span
                      className={cn("rounded-md px-2 py-0.5 text-xs font-medium", STATE[n.state][1])}
                      title={n.error}
                    >
                      {STATE[n.state][0]}
                      {n.legacy ? " · 旧版本" : ""}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 text-subtle">{n.forward === null ? "—" : n.forward ? "开启" : "关闭"}</td>
                  <td className="tabular px-5 py-2.5 text-right text-subtle">{n.state === "ok" && n.ms !== undefined ? `${n.ms} ms` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ) : null}
    </div>
  )
}
