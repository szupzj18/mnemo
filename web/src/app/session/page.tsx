import { Suspense } from "react"
import { SessionView } from "./session-view"

// The session is chosen by query string (?path=&host=&line=&q=), so the view
// reads useSearchParams and must sit under a Suspense boundary in a static export.
export default function SessionPage() {
  return (
    <Suspense fallback={<div className="py-16 text-center text-sm text-faint">正在读取会话…</div>}>
      <SessionView />
    </Suspense>
  )
}
