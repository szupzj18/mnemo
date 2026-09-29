// Flattens a topology probe (a tree of what each device reported) into a graph.

import type { TopoNeighbor, TopoNode } from "./api"

export type TopoState = "ok" | "bad" | "unknown"

export interface GraphNode {
  key: string
  id: string | null
  /** Route from this device: "local", "devbox-109", "devbox-109/devbox-126". */
  route: string
  /** Name the device gives itself (mnemo node --name), when known. */
  nodeName: string | null
  depth: number
  state: TopoState
  forward: boolean | null
  legacy: boolean
  host?: string
  ms?: number
  error?: string
}

export interface GraphEdge {
  from: string
  to: string
  state: TopoState
  ms?: number
}

export interface Graph {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

const neighborState = (n: TopoNeighbor): TopoState => (n.ok === true ? "ok" : n.ok === false ? "bad" : "unknown")

/**
 * Breadth-first, so every device keeps its shortest route (the one searches use),
 * and a device reached over several links appears once with an edge per link.
 */
export function buildGraph(root: TopoNode): Graph {
  const nodes = new Map<string, GraphNode>()
  const edges = new Map<string, GraphEdge>()
  nodes.set(root.id, {
    key: root.id,
    id: root.id,
    route: "local",
    nodeName: root.name,
    depth: 0,
    state: "ok",
    forward: root.forward,
    legacy: false,
  })
  const queue: { key: string; route: string; depth: number; tree: TopoNode }[] = [
    { key: root.id, route: "", depth: 0, tree: root },
  ]
  while (queue.length) {
    const parent = queue.shift()!
    for (const n of parent.tree.neighbors) {
      const route = parent.route ? parent.route + "/" + n.name : n.name
      const key = n.node_id ?? n.node?.id ?? "route:" + route
      const edgeKey = [parent.key, key].sort().join("|")
      if (!edges.has(edgeKey)) edges.set(edgeKey, { from: parent.key, to: key, state: neighborState(n), ms: n.ms })
      const known = nodes.get(key)
      // A device first listed without a probe (hop budget, upstream) is filled in if a later link probed it.
      if (known && (known.state !== "unknown" || n.ok === undefined)) continue
      nodes.set(key, {
        key,
        id: n.node?.id ?? n.node_id,
        route: known?.route ?? route,
        nodeName: n.node?.name ?? known?.nodeName ?? null,
        depth: known?.depth ?? parent.depth + 1,
        state: neighborState(n),
        forward: n.node ? n.node.forward : (known?.forward ?? null),
        legacy: Boolean(n.legacy),
        host: n.host ?? known?.host,
        ms: n.ms,
        error: n.error,
      })
      if (n.node) queue.push({ key, route: known?.route ?? route, depth: known?.depth ?? parent.depth + 1, tree: n.node })
    }
  }
  return { nodes: [...nodes.values()], edges: [...edges.values()] }
}

/** Direct neighbors' relay policy as reported by the probe, by remotes.json name. */
export function neighborForward(root: TopoNode | undefined): Record<string, boolean | null> {
  const out: Record<string, boolean | null> = {}
  for (const n of root?.neighbors ?? []) out[n.name] = n.node ? n.node.forward : null
  return out
}

export interface Positioned extends GraphNode {
  x: number
  y: number
}

/** Columns by hop count, devices spread evenly down each column. */
export function layout(graph: Graph, colWidth: number, rowHeight: number): { nodes: Positioned[]; width: number; height: number } {
  const cols = new Map<number, GraphNode[]>()
  for (const n of graph.nodes) cols.set(n.depth, [...(cols.get(n.depth) ?? []), n])
  const rows = Math.max(1, ...[...cols.values()].map((c) => c.length))
  const height = rows * rowHeight
  const positioned: Positioned[] = []
  for (const [depth, col] of [...cols.entries()].sort((a, b) => a[0] - b[0])) {
    col.sort((a, b) => a.route.localeCompare(b.route))
    col.forEach((n, i) => {
      positioned.push({ ...n, x: depth * colWidth + colWidth / 2, y: (height / col.length) * (i + 0.5) })
    })
  }
  return { nodes: positioned, width: Math.max(1, cols.size) * colWidth, height }
}
