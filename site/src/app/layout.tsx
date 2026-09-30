import type { Metadata } from "next"
import { GeistMono } from "geist/font/mono"
import { GeistSans } from "geist/font/sans"
import { asset } from "@/lib/site"
import "./globals.css"

const description =
  "Memory your coding agents call as a tool: every Claude Code, Codex, OpenCode and Pi session on your machines, searched in ~50 ms, across devices and through relays. Local, zero-dependency, MCP-native."

export const metadata: Metadata = {
  title: "Mnemo — one memory for all your coding agents",
  description,
  icons: { icon: asset("/img/logo-small.svg") },
  openGraph: {
    title: "Mnemo — one memory for all your coding agents",
    description,
    images: [asset("/img/search.png")],
    type: "website",
  },
  twitter: { card: "summary_large_image" },
}

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body className="font-sans">{children}</body>
    </html>
  )
}
