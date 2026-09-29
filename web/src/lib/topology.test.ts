import { describe, expect, it } from "vitest"
import type { TopoNeighbor, TopoNode } from "./api"
import { buildGraph, layout, neighborForward } from "./topology"

const node = (id: string, forward: boolean, neighbors: TopoNeighbor[] = []): TopoNode => ({
  id,
  name: "name-" + id,
  forward,
  neighbors,
})
const link = (name: string, child: TopoNode | null, extra: Partial<TopoNeighbor> = {}): TopoNeighbor => ({
  name,
  node_id: child?.id ?? null,
  ok: true,
  ms: 40,
  node: child,
  ...extra,
})

describe("buildGraph", () => {
  it("routes a chain through a relay", () => {
    const c = node("c", false)
    const b = node("b", true, [link("devbox-126", c)])
    const g = buildGraph(node("a", false, [link("devbox-109", b, { host: "user@109" })]))
    expect(g.nodes.map((n) => [n.route, n.depth, n.state])).toEqual([
      ["local", 0, "ok"],
      ["devbox-109", 1, "ok"],
      ["devbox-109/devbox-126", 2, "ok"],
    ])
    expect(g.nodes[1]).toMatchObject({ forward: true, host: "user@109", nodeName: "name-b" })
    expect(g.edges).toHaveLength(2)
  })

  it("keeps a device reached twice once, via the shortest route, with both links", () => {
    const c = node("c", false)
    const b = node("b", true, [link("c", null, { node_id: "c", ok: undefined, seen: true })])
    const g = buildGraph(node("a", false, [link("b", b), link("c", c)]))
    expect(g.nodes.map((n) => n.route)).toEqual(["local", "b", "c"])
    expect(g.nodes.find((n) => n.key === "c")?.state).toBe("ok")
    expect(g.edges.map((e) => [e.from, e.to])).toContainEqual(["b", "c"])
    expect(g.edges).toHaveLength(3)
  })

  it("marks unreachable, unprobed and legacy devices", () => {
    const b = node("b", true, [link("far", null, { node_id: null, ok: undefined, ms: undefined })])
    const g = buildGraph(
      node("a", true, [
        link("b", b),
        link("down", null, { ok: false, error: "timed out" }),
        link("old", null, { node_id: "o", legacy: true }),
      ]),
    )
    const by = Object.fromEntries(g.nodes.map((n) => [n.route, n]))
    expect(by["down"]).toMatchObject({ state: "bad", error: "timed out", key: "route:down" })
    expect(by["old"]).toMatchObject({ state: "ok", legacy: true, forward: null })
    expect(by["b/far"]).toMatchObject({ state: "unknown", depth: 2 })
    expect(g.edges.find((e) => e.to === "route:down")?.state).toBe("bad")
  })

  it("survives cycles", () => {
    const c: TopoNode = node("c", true)
    const b = node("b", true, [link("c", c)])
    c.neighbors = [link("b", null, { node_id: "b", ok: undefined, seen: true })]
    const g = buildGraph(node("a", false, [link("b", b)]))
    expect(g.nodes).toHaveLength(3)
    expect(g.edges).toHaveLength(2)
  })
})

describe("neighborForward", () => {
  it("reads direct neighbors' relay policy", () => {
    const root = node("a", false, [link("b", node("b", true)), link("x", null, { ok: false })])
    expect(neighborForward(root)).toEqual({ b: true, x: null })
    expect(neighborForward(undefined)).toEqual({})
  })
})

describe("layout", () => {
  it("places hops in columns and spreads each column", () => {
    const g = buildGraph(node("a", false, [link("b", node("b", false)), link("c", node("c", false))]))
    const { nodes, width, height } = layout(g, 200, 100)
    expect([width, height]).toEqual([400, 200])
    const pos = Object.fromEntries(nodes.map((n) => [n.route, [n.x, n.y]]))
    expect(pos).toEqual({ local: [100, 100], b: [300, 50], c: [300, 150] })
  })
})
