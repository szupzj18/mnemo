"use client"

import { useState } from "react"
import { Check, Copy } from "lucide-react"

export function CopyCommand({ command, className = "" }: { command: string; className?: string }) {
  const [copied, setCopied] = useState(false)
  const multiline = command.includes("\n")
  return (
    <div
      className={`group flex ${multiline ? "items-start" : "items-center"} gap-3 rounded-xl border border-neutral-200 bg-neutral-50 py-2.5 pr-2.5 pl-4 font-mono text-[13px] dark:border-neutral-800 dark:bg-neutral-900 ${className}`}
    >
      {multiline ? null : <span className="select-none text-neutral-400">$</span>}
      <code className="min-w-0 flex-1 overflow-x-auto py-1 whitespace-pre [scrollbar-width:none]">{command}</code>
      <button
        type="button"
        aria-label={copied ? "Copied" : "Copy command"}
        onClick={() =>
          navigator.clipboard.writeText(command).then(() => {
            setCopied(true)
            setTimeout(() => setCopied(false), 1500)
          })
        }
        className="grid size-8 shrink-0 place-items-center rounded-lg text-neutral-500 transition hover:bg-neutral-200 hover:text-neutral-900 dark:hover:bg-neutral-800 dark:hover:text-white"
      >
        {copied ? <Check className="size-4 text-emerald-500" /> : <Copy className="size-4" />}
      </button>
    </div>
  )
}
