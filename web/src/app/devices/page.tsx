"use client"

import * as React from "react"
import { Loader2 } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { ConfirmButton } from "@/components/confirm-button"
import { DeviceCard, remoteState } from "@/components/device-card"
import { RemoteDescription } from "@/components/remote-description"
import { srcSummary } from "@/lib/format"
import { useStore } from "@/lib/store"

function AddRemoteForm() {
  const { addRemote } = useStore()
  const [name, setName] = React.useState("")
  const [host, setHost] = React.useState("")
  const [bin, setBin] = React.useState("")
  const [busy, setBusy] = React.useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (busy) return
    if (!name.trim()) {
      toast.error("需要填写设备名称")
      return
    }
    setBusy(true)
    const ok = await addRemote(name.trim(), host.trim() || name.trim(), bin.trim())
    setBusy(false)
    if (ok) {
      setName("")
      setHost("")
      setBin("")
    }
  }

  return (
    <Card className="gap-3 px-6 py-5">
      <h2 className="text-sm font-semibold">添加远程设备</h2>
      <form className="flex flex-wrap items-center gap-2.5" onSubmit={submit}>
        <Input className="w-44" placeholder="名称，如 devbox-1" value={name} onChange={(e) => setName(e.target.value)} aria-label="设备名称" />
        <Input className="w-44" placeholder="SSH host（默认同名称）" value={host} onChange={(e) => setHost(e.target.value)} aria-label="SSH host" />
        <Input className="min-w-60 flex-1" placeholder="远端启动器路径（可选）" value={bin} onChange={(e) => setBin(e.target.value)} aria-label="远端启动器路径" />
        <Button type="submit" disabled={busy}>
          {busy ? <Loader2 className="animate-spin" /> : null}
          {busy ? "安装中" : "rsync 安装并建索引"}
        </Button>
      </form>
      <p className="text-xs text-faint">要求：免密 SSH、远端 Python 3.7+ 且 SQLite 支持 FTS5。设备上的会话正文不会被复制到本机。</p>
    </Card>
  )
}

export default function DevicesPage() {
  const { status, ping, rstat, checking, pingAll, pingOne, remoteStatus, syncOne, syncAll, removeRemote, updateRemotes } =
    useStore()
  const remotes = status?.remotes ?? []

  return (
    <div className="flex flex-col gap-4.5">
      <AddRemoteForm />
      <Card className="gap-4 px-6 py-5">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-sm font-semibold">已注册设备</h2>
          <span className="flex-1" />
          <Button variant="outline" size="sm" onClick={() => void pingAll(false)}>
            全部测试
          </Button>
          <Button variant="outline" size="sm" onClick={syncAll}>
            全部同步
          </Button>
          <ConfirmButton
            title="更新全部设备"
            description={`将向 ${remotes.length} 台设备重新 rsync 代码并增量建索引。`}
            onConfirm={() => void updateRemotes()}
          >
            全部更新代码
          </ConfirmButton>
        </div>
        <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-3.5">
          {remotes.map((r) => {
            const state = remoteState(r.name, ping, checking)
            const stat = rstat[r.name]
            return (
              <DeviceCard
                key={r.name}
                name={r.name}
                tag={r.host}
                state={state}
                description={
                  <>
                    <RemoteDescription state={state} ping={ping[r.name]} stat={stat} />
                    <span className="mt-1 block font-mono text-xs text-faint">bin: {r.bin}</span>
                  </>
                }
                sources={stat && state === "ok" ? srcSummary(stat.sources) : undefined}
                lastSync={stat ? stat.last_sync : undefined}
                actions={
                  <>
                    <Button variant="outline" size="sm" onClick={() => void pingOne(r.name)}>
                      测试
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => void remoteStatus(r.name)}>
                      索引状态
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => void syncOne(r.name)}>
                      同步
                    </Button>
                    <ConfirmButton
                      title={`更新 ${r.name} 的代码`}
                      description="将重新 rsync 代码并在该设备上增量建索引。"
                      onConfirm={() => void updateRemotes(r.name)}
                    >
                      更新代码
                    </ConfirmButton>
                    <ConfirmButton
                      title={`移除设备 ${r.name}`}
                      description="只删除本机的连接配置，不会删除设备上的任何文件。"
                      destructive
                      variant="destructive"
                      onConfirm={() => void removeRemote(r.name)}
                    >
                      移除
                    </ConfirmButton>
                  </>
                }
              />
            )
          })}
          {!remotes.length ? (
            <div className="col-span-full py-10 text-center text-[13px] text-faint">尚未注册设备</div>
          ) : null}
        </div>
      </Card>
    </div>
  )
}
