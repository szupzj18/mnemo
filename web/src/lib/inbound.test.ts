import { describe, expect, it } from "vitest"
import type { LinkInfo } from "./api"
import { linkLabel, linkOn, settling } from "./inbound"

const info = (p: Partial<LinkInfo>): LinkInfo => ({ remote: "b", installed: true, state: "connected", since: 1, error: null, ...p })

describe("inbound links", () => {
  it("labels states and shows why a link is not up", () => {
    expect(linkLabel(info({}))).toEqual({ text: "已连接", tone: "ok", detail: undefined })
    expect(linkLabel(info({ state: "retrying", error: "b: timed out" }))).toEqual({ text: "重连中", tone: "warn", detail: "b: timed out" })
    expect(linkLabel(info({ installed: false, state: "off" })).text).toBe("关闭")
    expect(linkLabel(undefined).text).toBe("—")
  })

  it("counts a hand-started link as on", () => {
    expect(linkOn(info({}))).toBe(true)
    expect(linkOn(info({ installed: false, state: "connected" }))).toBe(true)
    expect(linkOn(info({ installed: false, state: "off" }))).toBe(false)
    expect(linkOn(info({ installed: false, state: "stopped" }))).toBe(false)
  })

  it("keeps polling until the switch has taken effect", () => {
    expect(settling(info({ state: "connecting" }), true)).toBe(true)
    expect(settling(info({ state: "connected" }), true)).toBe(false)
    expect(settling(info({ state: "retrying" }), true)).toBe(false)
    expect(settling(info({ state: "connected" }), false)).toBe(true)
    expect(settling(info({ installed: false, state: "off" }), false)).toBe(false)
  })
})
