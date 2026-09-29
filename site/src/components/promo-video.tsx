import { ArrowUpRight } from "lucide-react"
import { asset } from "@/lib/site"

/**
 * The promo animation, embedded from public/promo/. It is one self-contained page
 * (fonts inlined) that draws every frame from a clock, so an iframe is all it needs.
 * `embed=1` drops its page padding and note so the frame is the stage plus the
 * control bar: 16:9 of the width, plus the bar's 56px.
 */
export function PromoVideo() {
  const page = asset("/promo/index.html")
  return (
    <div>
      <div
        className="relative w-full overflow-hidden rounded-2xl border border-neutral-200 bg-[#16181a] shadow-xl shadow-neutral-900/5 dark:border-neutral-800"
        style={{ paddingBottom: "calc(56.25% + 56px)" }}
      >
        <iframe
          src={`${page}?embed=1&lang=en`}
          title="Mnemo promo animation"
          loading="lazy"
          allow="fullscreen"
          className="absolute inset-0 h-full w-full border-0"
        />
      </div>
      <a
        href={`${page}?lang=en`}
        className="mt-4 inline-flex items-center gap-1 text-sm text-neutral-600 hover:text-neutral-950 dark:text-neutral-400 dark:hover:text-white"
      >
        Open full screen, with English and 中文 <ArrowUpRight className="size-3.5" />
      </a>
    </div>
  )
}
