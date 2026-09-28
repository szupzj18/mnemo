import { tokenizeBody, type Inline } from "@/lib/transcript"
import { cn } from "@/lib/utils"

function Inlines({ runs }: { runs: Inline[] }) {
  return (
    <>
      {runs.map((r, i) =>
        r.t === "mark" ? (
          <mark key={i}>{r.v}</mark>
        ) : r.t === "code" ? (
          <code key={i} className="rounded border bg-muted px-1 py-px font-mono text-[0.9em]">
            <Inlines runs={r.v} />
          </code>
        ) : (
          <span key={i}>{r.v}</span>
        ),
      )}
    </>
  )
}

/** Message text with fenced code blocks, `inline code` and highlighted search terms. */
export function Body({ text, terms, mono, className }: { text: string; terms: string[]; mono?: boolean; className?: string }) {
  const blocks = tokenizeBody(text, terms)
  return (
    <div className={cn("text-[14.5px] leading-relaxed break-words", mono && "font-mono text-[12.5px] leading-normal", className)}>
      {blocks.map((b, i) =>
        b.t === "codeblock" ? (
          <pre key={i} className="my-2 overflow-x-auto rounded-lg border bg-muted px-3.5 py-2.5 font-mono text-[12.5px] leading-normal">
            <Inlines runs={b.v} />
          </pre>
        ) : (
          <div key={i} className="whitespace-pre-wrap">
            <Inlines runs={b.v} />
          </div>
        ),
      )}
    </div>
  )
}
