"use client"

import * as React from "react"
import { toast } from "sonner"
import {
  api,
  type Hit,
  type HostResult,
  type NodeInfo,
  type Ping,
  type RemoteStatus,
  type Status,
  type TopologyResult,
} from "./api"
import { fmtSecs, totals } from "./format"

export interface LogLine {
  id: number
  at: Date
  text: string
  level: "info" | "ok" | "err"
}

export interface SearchState {
  query: string
  limit: number
  allHosts: boolean
  picked: string[]
  running: boolean
  startedAt: number | null
  targets: string[]
  perHost: HostResult[]
  hits: Hit[]
  warnings: string[]
  terms: string[]
  took: string | null
  error: string | null
  searched: boolean
}

interface Store {
  status: Status | null
  ping: Record<string, Ping>
  rstat: Record<string, RemoteStatus>
  checking: Set<string>
  logs: LogLine[]
  search: SearchState
  node: NodeInfo | null
  topology: TopologyResult | null
  topologyLoading: boolean
  probeTopology: () => Promise<void>
  saveNode: (patch: Partial<Pick<NodeInfo, "name" | "forward">>) => Promise<boolean>
  setRemoteForward: (name: string, forward: boolean) => Promise<void>
  /** Routes being brought to this device's code; "*" while updating every device. */
  upgrading: Set<string>
  upgradeDevices: (routes?: string[]) => Promise<void>
  refresh: () => Promise<void>
  probeRemotes: () => Promise<void>
  pingAll: (quiet?: boolean) => Promise<void>
  pingOne: (name: string) => Promise<void>
  remoteStatus: (name: string) => Promise<void>
  syncLocal: () => Promise<void>
  syncOne: (name: string) => Promise<void>
  syncAll: () => void
  addRemote: (name: string, host: string, bin: string) => Promise<boolean>
  removeRemote: (name: string) => Promise<void>
  updateRemotes: (name?: string) => Promise<void>
  setSearch: (patch: Partial<SearchState>) => void
  runSearch: () => Promise<void>
  log: (text: string, level?: LogLine["level"]) => void
  clearLogs: () => void
}

const StoreContext = React.createContext<Store | null>(null)

export function useStore(): Store {
  const s = React.useContext(StoreContext)
  if (!s) throw new Error("useStore outside StoreProvider")
  return s
}

const initialSearch: SearchState = {
  query: "",
  limit: 20,
  allHosts: true,
  picked: [],
  running: false,
  startedAt: null,
  targets: [],
  perHost: [],
  hits: [],
  warnings: [],
  terms: [],
  took: null,
  error: null,
  searched: false,
}

const errText = (e: unknown) => String((e as Error)?.message ?? e)

function omit<T>(m: Record<string, T>, key: string): Record<string, T> {
  const n = { ...m }
  delete n[key]
  return n
}

export function StoreProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = React.useState<Status | null>(null)
  const [ping, setPing] = React.useState<Record<string, Ping>>({})
  const [rstat, setRstat] = React.useState<Record<string, RemoteStatus>>({})
  const [checking, setChecking] = React.useState<Set<string>>(new Set())
  const [logs, setLogs] = React.useState<LogLine[]>([])
  const [search, setSearchState] = React.useState<SearchState>(initialSearch)
  const [node, setNode] = React.useState<NodeInfo | null>(null)
  const [topology, setTopology] = React.useState<TopologyResult | null>(null)
  const [topologyLoading, setTopologyLoading] = React.useState(false)
  const [upgrading, setUpgrading] = React.useState<Set<string>>(new Set())
  const logId = React.useRef(0)
  const statusRef = React.useRef<Status | null>(null)
  const searchRef = React.useRef(search)
  React.useLayoutEffect(() => {
    searchRef.current = search
  }, [search])

  const log = React.useCallback((text: string, level: LogLine["level"] = "info") => {
    setLogs((l) => [...l, { id: ++logId.current, at: new Date(), text, level }])
  }, [])

  const notify = React.useCallback(
    (msg: string, level: "info" | "ok" | "err" = "info", sub?: string) => {
      const fn = level === "ok" ? toast.success : level === "err" ? toast.error : toast
      fn(msg, sub ? { description: sub } : undefined)
      log(msg + (sub ? "  " + sub : ""), level)
    },
    [log],
  )

  const refresh = React.useCallback(async () => {
    try {
      const s = await api.status()
      if (s.error) throw new Error(s.error)
      statusRef.current = s
      setStatus(s)
    } catch (e) {
      notify("状态获取失败", "err", errText(e))
    }
  }, [notify])

  const markChecking = (name: string, on: boolean) =>
    setChecking((c) => {
      const n = new Set(c)
      if (on) n.add(name)
      else n.delete(name)
      return n
    })

  const loadRemote = React.useCallback(async (name: string) => {
    markChecking(name, true)
    try {
      const r = await api.remoteStatus(name)
      if (r.ok) {
        setRstat((m) => ({ ...m, [name]: r.status }))
        setPing((m) => ({ ...m, [name]: { name, ok: true, ms: r.status.ms } }))
        return r.status
      }
      setPing((m) => ({ ...m, [name]: { name, ok: false, error: r.error } }))
      throw new Error(r.error)
    } finally {
      markChecking(name, false)
    }
  }, [])

  const probeRemotes = React.useCallback(async () => {
    const remotes = statusRef.current?.remotes ?? []
    await Promise.all(remotes.map((r) => loadRemote(r.name).catch(() => undefined)))
  }, [loadRemote])

  const pingAll = React.useCallback(
    async (quiet = false) => {
      try {
        const r = await api.ping()
        setPing((m) => {
          const n = { ...m }
          for (const x of r.results) n[x.name] = x
          return n
        })
        if (!quiet)
          r.results.forEach((x) =>
            x.ok ? notify(`${x.name} 连接正常`, "ok", `${x.ms} ms`) : notify(`${x.name} 不可达`, "err", x.error),
          )
        log(
          "连接测试：" + (r.results.map((x) => x.name + (x.ok ? " ✓" : " ✗")).join("，") || "没有远程设备"),
          r.results.every((x) => x.ok) ? "ok" : "err",
        )
      } catch (e) {
        notify("连接测试失败", "err", errText(e))
      }
    },
    [log, notify],
  )

  const pingOne = React.useCallback(
    async (name: string) => {
      try {
        const x = (await api.ping(name)).results[0]
        setPing((m) => ({ ...m, [name]: x }))
        if (x.ok) notify(`${name} 连接正常`, "ok", `${x.ms} ms`)
        else notify(`${name} 不可达`, "err", x.error)
      } catch (e) {
        notify(`${name} 测试失败`, "err", errText(e))
      }
    },
    [notify],
  )

  const remoteStatus = React.useCallback(
    async (name: string) => {
      try {
        const s = await loadRemote(name)
        const t = totals(s.sources)
        notify(`${name} 索引状态`, "ok", `${t.files} 个会话 · ${t.msgs.toLocaleString()} 条消息`)
      } catch (e) {
        notify(`${name} 状态获取失败`, "err", errText(e))
      }
    },
    [loadRemote, notify],
  )

  const syncLocal = React.useCallback(async () => {
    const id = toast.loading("本机增量同步中…")
    try {
      const r = await api.syncLocal()
      toast.dismiss(id)
      if (!r.ok) throw new Error(r.error)
      notify("本机同步完成", "ok", `+${r.stats.files_new} 新 / ${r.stats.messages} 消息 / ${r.ms} ms`)
      await refresh()
    } catch (e) {
      toast.dismiss(id)
      notify("本机同步失败", "err", errText(e))
    }
  }, [notify, refresh])

  const syncOne = React.useCallback(
    async (name: string) => {
      const id = toast.loading(`${name} 增量同步中…`)
      try {
        const r = await api.syncRemote(name)
        toast.dismiss(id)
        if (!r.ok) throw new Error(r.error)
        notify(`${name} 同步完成`, "ok", `${r.output} / ${r.ms} ms`)
        await loadRemote(name).catch(() => undefined)
      } catch (e) {
        toast.dismiss(id)
        notify(`${name} 同步失败`, "err", errText(e))
      }
    },
    [loadRemote, notify],
  )

  const syncAll = React.useCallback(() => {
    void syncLocal()
    ;(statusRef.current?.remotes ?? []).forEach((r) => void syncOne(r.name))
  }, [syncLocal, syncOne])

  const addRemote = React.useCallback(
    async (name: string, host: string, bin: string) => {
      notify(`正在连接 ${host} 并安装，通常需要几十秒…`)
      try {
        const r = await api.addRemote(name, host, bin)
        if (!r.ok) throw new Error(r.error)
        notify(`已添加设备 ${name}`, "ok", r.logs.join(" / "))
        await refresh()
        await loadRemote(name).catch(() => undefined)
        return true
      } catch (e) {
        notify("添加失败", "err", errText(e))
        return false
      }
    },
    [loadRemote, notify, refresh],
  )

  const removeRemote = React.useCallback(
    async (name: string) => {
      try {
        const r = await api.removeRemote(name)
        if (!r.ok) throw new Error(r.error)
        setPing((m) => omit(m, name))
        setRstat((m) => omit(m, name))
        notify(`已移除 ${name}`, "ok")
        await refresh()
      } catch (e) {
        notify("移除失败", "err", errText(e))
      }
    },
    [notify, refresh],
  )

  const updateRemotes = React.useCallback(
    async (name?: string) => {
      const label = name ?? "全部设备"
      const id = toast.loading(`正在更新 ${label}…`)
      try {
        const r = await api.updateRemotes(name)
        toast.dismiss(id)
        if (!r.ok) throw new Error(r.error)
        notify(`更新完成：${label}`, "ok", r.logs.join(" / "))
      } catch (e) {
        toast.dismiss(id)
        notify(`更新失败：${label}`, "err", errText(e))
      }
    },
    [notify],
  )

  // Bumped on every local node change: a probe that started before one must not
  // put the old name or relay setting back when it returns.
  const nodeGen = React.useRef(0)
  const nodeRef = React.useRef<NodeInfo | null>(null)
  React.useEffect(() => {
    nodeRef.current = node
  }, [node])

  const probeTopology = React.useCallback(async () => {
    setTopologyLoading(true)
    const gen = nodeGen.current
    try {
      const r = await api.topology()
      if (!r.ok) throw new Error(r.error)
      let t = r.topology
      const saved = nodeRef.current
      if (gen !== nodeGen.current && saved) t = { ...t, name: saved.name, forward: saved.forward }
      else setNode({ id: t.id, name: t.name, forward: t.forward, code: t.code })
      setTopology({ ms: r.ms, ttl: r.ttl, topology: t })
    } catch (e) {
      notify("拓扑探测失败", "err", errText(e))
    } finally {
      setTopologyLoading(false)
    }
  }, [notify])

  const saveNode = React.useCallback(
    async (patch: Partial<Pick<NodeInfo, "name" | "forward">>) => {
      try {
        nodeGen.current++
        const r = await api.setNode(patch)
        if (!r.ok) throw new Error(r.error)
        setNode({ id: r.id, name: r.name, forward: r.forward, code: r.code })
        setTopology((t) => (t ? { ...t, topology: { ...t.topology, name: r.name, forward: r.forward } } : t))
        if ("forward" in patch) notify(r.forward ? "本机已开启中转" : "本机已关闭中转", "ok")
        else notify(`本机已更名为 ${r.name}`, "ok")
        return true
      } catch (e) {
        notify("保存失败", "err", errText(e))
        return false
      }
    },
    [notify],
  )

  const setRemoteForward = React.useCallback(
    async (name: string, forward: boolean) => {
      try {
        const r = await api.setRemoteNode(name, { forward })
        if (!r.ok) throw new Error(r.error)
        notify(`${name} 已${r.node.forward ? "开启" : "关闭"}中转`, "ok")
        await probeTopology()
      } catch (e) {
        notify(`${name} 中转设置失败`, "err", errText(e))
      }
    },
    [notify, probeTopology],
  )

  const upgradeDevices = React.useCallback(
    async (routes?: string[]) => {
      const keys = routes ?? ["*"]
      setUpgrading((s) => new Set([...s, ...keys]))
      const label = routes ? routes.join("、") : "全部落后设备"
      const id = toast.loading(`正在更新 ${label}…`)
      try {
        const r = await api.upgradeDevices(routes)
        toast.dismiss(id)
        if (!r.ok) throw new Error(r.error)
        const updated = r.results.filter((x) => x.status === "updated").map((x) => x.route)
        const failed = r.results.filter((x) => x.status === "failed")
        const detail = [
          ...failed.map((x) => `${x.route}：${x.error ?? "失败"}`),
          ...r.warnings,
        ].join(" / ")
        if (failed.length) notify(`${failed.length} 台设备更新失败`, "err", detail)
        else if (updated.length) notify(`已更新 ${updated.join("、")}`, "ok", detail || undefined)
        else notify("所有设备都已是最新代码", "ok", detail || undefined)
      } catch (e) {
        toast.dismiss(id)
        notify(`更新失败：${label}`, "err", errText(e))
      } finally {
        setUpgrading((s) => new Set([...s].filter((k) => !keys.includes(k))))
      }
      await probeTopology()
    },
    [notify, probeTopology],
  )

  const setSearch = React.useCallback((patch: Partial<SearchState>) => {
    setSearchState((s) => ({ ...s, ...patch }))
  }, [])

  const runSearch = React.useCallback(async () => {
    const s = searchRef.current
    const query = s.query.trim()
    if (!query || s.running) return
    const hosts = s.allHosts ? null : s.picked
    const targets = hosts ?? ["local", ...(statusRef.current?.remotes ?? []).map((r) => r.name)]
    const t0 = Date.now()
    setSearchState((cur) => ({ ...cur, running: true, startedAt: t0, targets, error: null, searched: true }))
    try {
      const r = await api.search(query, s.limit, hosts)
      if (r.error) throw new Error(r.error)
      const took = fmtSecs(Date.now() - t0)
      setSearchState((cur) => ({
        ...cur,
        running: false,
        startedAt: null,
        perHost: r.per_host,
        hits: r.merged,
        warnings: r.warnings,
        terms: query.split(/\s+/).filter(Boolean),
        took,
      }))
      log(`搜索「${query}」：` + r.per_host.map((h) => h.host + " " + (h.ok ? h.ms + "ms" : "✗")).join("，"))
    } catch (e) {
      const msg = errText(e)
      setSearchState((cur) => ({
        ...cur,
        running: false,
        startedAt: null,
        perHost: [],
        hits: [],
        warnings: [],
        took: null,
        error: msg,
      }))
      notify("搜索失败", "err", msg)
    }
  }, [log, notify])

  const value: Store = {
    status,
    ping,
    rstat,
    checking,
    logs,
    search,
    node,
    topology,
    topologyLoading,
    probeTopology,
    saveNode,
    setRemoteForward,
    upgrading,
    upgradeDevices,
    refresh,
    probeRemotes,
    pingAll,
    pingOne,
    remoteStatus,
    syncLocal,
    syncOne,
    syncAll,
    addRemote,
    removeRemote,
    updateRemotes,
    setSearch,
    runSearch,
    log,
    clearLogs: () => setLogs([]),
  }

  // Load status once, then probe every remote so device cards show live state.
  React.useEffect(() => {
    let alive = true
    api
      .status()
      .then((s) => {
        if (!alive || s.error) return
        statusRef.current = s
        setStatus(s)
        void probeRemotes()
      })
      .catch((e) => toast.error("状态获取失败", { description: errText(e) }))
    api
      .node()
      .then((n) => {
        if (alive && n.id) setNode(n)
      })
      .catch((e) => toast.error("状态获取失败", { description: errText(e) }))
    return () => {
      alive = false
    }
  }, [probeRemotes])

  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>
}
