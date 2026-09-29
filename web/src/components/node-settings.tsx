"use client"

import * as React from "react"
import { Check, Pencil, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { ForwardDialog } from "@/components/forward-dialog"
import { useStore } from "@/lib/store"

/** This device's name and relay switch (mnemo node). */
export function NodeSettings() {
  const { node, saveNode } = useStore()
  const [editing, setEditing] = React.useState(false)
  const [draft, setDraft] = React.useState("")
  const [confirming, setConfirming] = React.useState(false)
  const [busy, setBusy] = React.useState(false)

  if (!node) return null

  async function rename(e: React.FormEvent) {
    e.preventDefault()
    const name = draft.trim()
    if (!name || name === node?.name) {
      setEditing(false)
      return
    }
    setBusy(true)
    if (await saveNode({ name })) setEditing(false)
    setBusy(false)
  }

  async function setForward(forward: boolean) {
    setBusy(true)
    await saveNode({ forward })
    setBusy(false)
  }

  return (
    <Card className="gap-4 px-6 py-5" data-testid="node-settings">
      <div className="flex flex-wrap items-start gap-x-10 gap-y-4">
        <div className="flex min-w-0 flex-col gap-1.5">
          <span className="text-xs text-faint">本机节点名</span>
          {editing ? (
            <form className="flex items-center gap-1.5" onSubmit={rename}>
              <Input
                autoFocus
                className="h-8 w-52"
                value={draft}
                maxLength={64}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => e.key === "Escape" && setEditing(false)}
                aria-label="本机节点名"
              />
              <Button type="submit" size="icon-sm" aria-label="保存" disabled={busy}>
                <Check />
              </Button>
              <Button type="button" variant="ghost" size="icon-sm" aria-label="取消" onClick={() => setEditing(false)}>
                <X />
              </Button>
            </form>
          ) : (
            <div className="flex items-center gap-1.5">
              <span className="text-base font-bold tracking-tight" data-testid="node-name">
                {node.name}
              </span>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="重命名本机"
                onClick={() => {
                  setDraft(node.name)
                  setEditing(true)
                }}
              >
                <Pencil />
              </Button>
            </div>
          )}
          <span className="font-mono text-xs text-faint" title={node.id}>
            id {node.id.slice(0, 12)}
          </span>
        </div>
        <div className="flex max-w-2xl min-w-60 flex-1 flex-col gap-1.5">
          <label className="flex items-center gap-2.5 text-sm font-medium">
            <Switch
              checked={node.forward}
              disabled={busy}
              aria-label="本机中转"
              data-testid="node-forward"
              onCheckedChange={(on) => (on ? setConfirming(true) : void setForward(false))}
            />
            中转 {node.forward ? "已开启" : "已关闭"}
          </label>
          <p className="text-xs leading-relaxed text-faint">
            {node.forward
              ? "邻居设备可以经由本机搜索和读取本机注册的其他设备。"
              : "邻居只能搜索本机自己的会话；开启后，它们也能经由本机访问本机注册的其他设备。"}
          </p>
        </div>
      </div>
      <ForwardDialog device="本机" open={confirming} onOpenChange={setConfirming} onConfirm={() => void setForward(true)} />
    </Card>
  )
}
