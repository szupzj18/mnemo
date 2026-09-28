"use client"

import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { useStore } from "@/lib/store"
import { cn } from "@/lib/utils"

export default function LogsPage() {
  const { logs, clearLogs } = useStore()
  return (
    <Card className="gap-3 px-6 py-5">
      <div className="flex items-center">
        <h2 className="text-sm font-semibold">操作日志</h2>
        <span className="flex-1" />
        <Button variant="outline" size="sm" onClick={clearLogs}>
          清空
        </Button>
      </div>
      <div className="max-h-[70vh] overflow-auto rounded-lg border bg-muted/40 p-3 font-mono text-xs" data-testid="logbox">
        {logs.length ? (
          logs.map((l) => (
            <div key={l.id} className={cn("py-0.5", l.level === "err" && "text-err", l.level === "ok" && "text-ok")}>
              <span className="mr-3 text-faint">{l.at.toLocaleTimeString()}</span>
              {l.text}
            </div>
          ))
        ) : (
          <div className="py-8 text-center text-faint">暂无日志</div>
        )}
      </div>
    </Card>
  )
}
