// Three devices, each with its own index; a query flows out as a message and
// ranked hits flow back. Nothing is copied into a central store.
const NODES = [
  { x: 60, y: 60, label: "laptop", sub: "claude · codex · pi" },
  { x: 300, y: 30, label: "devbox-109", sub: "own index" },
  { x: 300, y: 150, label: "devbox-126", sub: "own index" },
]

export function Mesh() {
  return (
    <svg viewBox="0 0 400 200" className="w-full" role="img" aria-label="A search fans out from the laptop to two devboxes and merges back">
      {NODES.slice(1).map((n) => (
        <path
          key={n.label}
          d={`M${NODES[0].x + 36} ${NODES[0].y + 14} C 180 ${NODES[0].y + 14}, 180 ${n.y + 14}, ${n.x - 4} ${n.y + 14}`}
          fill="none"
          stroke="#4176e6"
          strokeWidth="1.6"
          strokeLinecap="round"
          className="flow"
        />
      ))}
      {NODES.map((n, i) => (
        <g key={n.label} transform={`translate(${n.x - 40} ${n.y - 6})`}>
          <rect
            width="80"
            height="40"
            rx="10"
            className={i === 0 ? "fill-neutral-950 dark:fill-white" : "fill-white stroke-neutral-300 dark:fill-neutral-900 dark:stroke-neutral-700"}
            strokeWidth="1"
          />
          <text x="40" y="18" textAnchor="middle" className={`text-[10px] font-medium ${i === 0 ? "fill-white dark:fill-neutral-950" : "fill-neutral-900 dark:fill-neutral-100"}`}>
            {n.label}
          </text>
          <text x="40" y="31" textAnchor="middle" className={`text-[8px] ${i === 0 ? "fill-neutral-400 dark:fill-neutral-500" : "fill-neutral-500"}`}>
            {n.sub}
          </text>
        </g>
      ))}
    </svg>
  )
}
