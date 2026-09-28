import type { Metadata } from "next"
import { ThemeProvider } from "next-themes"
import { AppShell } from "@/components/app-shell"
import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import { StoreProvider } from "@/lib/store"
import "./globals.css"

export const metadata: Metadata = {
  title: "mnemo 管理面板",
  description: "Local dashboard for cross-agent, cross-device session search.",
  icons: { icon: "/logo-small.svg" },
}

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="zh-CN" suppressHydrationWarning className="h-full antialiased">
      <head>
        {/* Replaced with the per-launch API token by the Python server. */}
        <meta name="mnemo-token" content="__MNEMO_TOKEN__" />
      </head>
      <body className="min-h-full">
        <ThemeProvider attribute="class" storageKey="mnemo-theme" defaultTheme="light" enableSystem={false}>
          <TooltipProvider>
            <StoreProvider>
              <AppShell>{children}</AppShell>
            </StoreProvider>
            <Toaster position="top-right" richColors={false} />
          </TooltipProvider>
        </ThemeProvider>
      </body>
    </html>
  )
}
