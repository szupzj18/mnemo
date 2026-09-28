import { cn } from "@/lib/utils"

/**
 * Mesh M: an M drawn as five device nodes; dashed links from the bottom nodes
 * to the blue center are queries passed between machines. Tile and ink follow
 * the theme through the primary / primary-foreground tokens.
 */
export function Logo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 64" aria-hidden="true" className={cn("size-7 shrink-0", className)}>
      <rect width="64" height="64" rx="15" className="fill-primary" />
      <path
        d="M17 46 32 35 47 46"
        fill="none"
        stroke="#4176e6"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeDasharray="0.1 5"
      />
      <path
        d="M17 46V19l15 16 15-16v27"
        fill="none"
        className="stroke-primary-foreground"
        strokeWidth="5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <g className="fill-primary-foreground">
        <circle cx="17" cy="46" r="5" />
        <circle cx="17" cy="19" r="5" />
        <circle cx="47" cy="19" r="5" />
        <circle cx="47" cy="46" r="5" />
      </g>
      <circle cx="32" cy="35" r="6" fill="#4176e6" className="stroke-primary" strokeWidth="2" />
    </svg>
  )
}
