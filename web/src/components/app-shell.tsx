"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { useTheme } from "next-themes"
import { LayoutGrid, Moon, RefreshCw, ScrollText, Search, Server, Sun, Wifi } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Logo } from "@/components/logo"
import { useStore } from "@/lib/store"
import { cn } from "@/lib/utils"

const NAV = [
  { href: "/", label: "仪表盘", icon: LayoutGrid },
  { href: "/devices/", label: "设备管理", icon: Server },
  { href: "/search/", label: "会话搜索", icon: Search },
  { href: "/logs/", label: "日志查看", icon: ScrollText },
]

const TITLES: Record<string, string> = {
  "/": "仪表盘",
  "/devices/": "设备管理",
  "/search/": "会话搜索",
  "/session/": "会话全文",
  "/logs/": "日志查看",
}

const norm = (p: string) => (p.endsWith("/") ? p : p + "/")

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = norm(usePathname() || "/")
  const { pingAll, refresh, probeRemotes } = useStore()
  const { resolvedTheme, setTheme } = useTheme()
  const activeHref = pathname === "/session/" ? "/search/" : pathname

  return (
    <div className="flex min-h-svh">
      <aside className="sticky top-0 flex h-svh w-[54px] shrink-0 flex-col border-r border-sidebar-border bg-sidebar px-2 py-4 md:w-[210px] md:px-3">
        <Link href="/" className="mb-5 flex items-center justify-center gap-2.5 px-1 md:justify-start md:px-2.5">
          <Logo />
          <span className="hidden text-[15px] font-bold md:inline">mnemo</span>
        </Link>
        <nav className="flex flex-col gap-0.5">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = activeHref === href
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex items-center justify-center gap-3 rounded-lg px-3 py-2 text-sm text-foreground/80 transition-colors hover:bg-sidebar-accent md:justify-start",
                  active && "bg-sidebar-accent font-medium text-foreground",
                )}
              >
                <Icon className={cn("size-[18px]", active && "text-brand")} />
                <span className="hidden md:inline">{label}</span>
              </Link>
            )
          })}
        </nav>
        <div className="mt-auto hidden px-2.5 text-xs text-faint md:block">v0.1.0 · 127.0.0.1 本地服务</div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 items-center gap-1 px-4 md:px-8">
          <h1 className="text-lg font-semibold" data-testid="page-title">
            {TITLES[pathname] ?? ""}
          </h1>
          <span className="flex-1" />
          <Button variant="ghost" onClick={() => void pingAll(false)}>
            <Wifi /> <span className="hidden sm:inline">测试连接</span>
          </Button>
          <Button variant="ghost" onClick={() => void refresh().then(probeRemotes)}>
            <RefreshCw /> <span className="hidden sm:inline">刷新</span>
          </Button>
          <Button
            variant="ghost"
            size="icon"
            aria-label="切换主题"
            data-testid="theme-toggle"
            onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
          >
            <Sun className="dark:hidden" />
            <Moon className="hidden dark:block" />
          </Button>
        </header>
        <main className="min-w-0 flex-1 px-4 pb-10 md:px-8">{children}</main>
      </div>
    </div>
  )
}
