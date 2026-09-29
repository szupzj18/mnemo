"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { useTheme } from "next-themes"
import { LayoutGrid, Moon, Network, RefreshCw, ScrollText, Search, Server, Sun, Wifi } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { Logo } from "@/components/logo"
import { useStore } from "@/lib/store"
import { cn } from "@/lib/utils"

const NAV = [
  { href: "/", label: "仪表盘", icon: LayoutGrid },
  { href: "/devices/", label: "设备管理", icon: Server },
  { href: "/topology/", label: "网络拓扑", icon: Network },
  { href: "/search/", label: "会话搜索", icon: Search },
  { href: "/logs/", label: "日志查看", icon: ScrollText },
]

const TITLES: Record<string, string> = {
  "/": "仪表盘",
  "/devices/": "设备管理",
  "/topology/": "网络拓扑",
  "/search/": "会话搜索",
  "/session/": "会话全文",
  "/logs/": "日志查看",
}

export const REPO_URL = "https://github.com/szupzj18/mnemo"

// GitHub mark from Octicons (MIT); lucide no longer ships brand icons.
function GitHubMark() {
  return (
    <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M8 0c4.42 0 8 3.58 8 8a8.013 8.013 0 0 1-5.45 7.59c-.4.08-.55-.17-.55-.38 0-.27.01-1.13.01-2.2 0-.75-.25-1.23-.54-1.48 1.78-.2 3.65-.88 3.65-3.95 0-.88-.31-1.59-.82-2.15.08-.2.36-1.02-.08-2.12 0 0-.67-.22-2.2.82-.64-.18-1.32-.27-2-.27-.68 0-1.36.09-2 .27-1.53-1.03-2.2-.82-2.2-.82-.44 1.1-.16 1.92-.08 2.12-.51.56-.82 1.28-.82 2.15 0 3.06 1.86 3.75 3.64 3.95-.23.2-.44.55-.51 1.07-.46.21-1.61.55-2.33-.66-.15-.24-.6-.83-1.23-.82-.67.01-.27.38.01.53.34.19.73.9.82 1.13.16.45.68 1.31 2.69.94 0 .67.01 1.3.01 1.49 0 .21-.15.45-.55.38A7.995 7.995 0 0 1 0 8c0-4.42 3.58-8 8-8Z" />
    </svg>
  )
}

const norm = (p: string) => (p.endsWith("/") ? p : p + "/")

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = norm(usePathname() || "/")
  const { status, pingAll, refresh, probeRemotes } = useStore()
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
        <div className="mt-auto hidden px-2.5 text-xs text-faint md:block">
          <span data-testid="app-version">{status?.version ? `v${status.version}` : "mnemo"}</span> · 127.0.0.1 本地服务
        </div>
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
          <Tooltip>
            <TooltipTrigger
              render={
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="在 GitHub 查看源码"
                  data-testid="github-link"
                  render={<a href={REPO_URL} target="_blank" rel="noopener noreferrer" />}
                  nativeButton={false}
                />
              }
            >
              <GitHubMark />
            </TooltipTrigger>
            <TooltipContent>在 GitHub 查看源码</TooltipContent>
          </Tooltip>
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
