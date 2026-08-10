import { NavLink, Outlet, useLocation } from 'react-router-dom'
import {
  Bot,
  CircleHelp,
  ClipboardCheck,
  FolderOpen,
  Moon,
  Settings,
  ShieldCheck,
  Sun,
} from 'lucide-react'
import { Explain, HelpModeBanner } from '@/components/explain'
import { useHelpMode } from '@/components/help-mode'
import { useTheme } from '@/components/theme-provider'
import { Button } from '@/components/ui/button'
import { helpText } from '@/lib/help-text'
import { cn } from '@/lib/utils'

const NAV = [
  { to: '/', label: '审查', icon: ClipboardCheck, end: true, help: helpText.nav.workbench },
  { to: '/knowledge-bases', label: '知识库', icon: FolderOpen, help: helpText.nav.knowledge },
  { to: '/analytics', label: '智能问答', icon: Bot, help: helpText.nav.analytics },
  { to: '/settings', label: '系统设置', icon: Settings, help: helpText.nav.settings },
] as const

function isNavActive(to: string, pathname: string, end?: boolean) {
  if (to === '/knowledge-bases') {
    return (
      pathname === '/knowledge-bases'
      || pathname.startsWith('/kb/')
      || pathname.startsWith('/chunk/')
    )
  }
  if (to === '/settings') return pathname.startsWith('/settings')
  if (to === '/analytics') return pathname.startsWith('/analytics')
  if (end) return pathname === to
  return pathname === to || pathname.startsWith(`${to}/`)
}

export function RootLayout() {
  const { theme, toggleTheme } = useTheme()
  const { enabled, toggle } = useHelpMode()
  const { pathname } = useLocation()

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-bg-canvas text-text-primary">
      <header className="relative z-20 flex h-16 shrink-0 items-center border-b border-[#e5e7eb] bg-white px-5 dark:border-border-button dark:bg-bg-base">
        <div className="flex min-w-0 items-center gap-2.5">
          <div
            className="grid size-9 shrink-0 place-items-center rounded-xl bg-[#13c2c2] text-white"
            aria-hidden
          >
            <ShieldCheck className="size-5" strokeWidth={2.25} />
          </div>
          <div className="hidden min-w-0 sm:block">
            <div className="truncate text-[17px] font-bold tracking-tight text-[#111827] dark:text-text-primary">
              标准RAG审查
            </div>
          </div>
        </div>

        <nav
          className="ml-3 flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto rounded-full bg-[#f3f4f6] p-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden dark:bg-bg-card sm:absolute sm:left-1/2 sm:top-1/2 sm:ml-0 sm:max-w-[min(100%-12rem,48rem)] sm:-translate-x-1/2 sm:-translate-y-1/2"
          aria-label="主导航"
        >
          {NAV.map(item => {
            const active = isNavActive(item.to, pathname, 'end' in item ? item.end : false)
            return (
              <Explain key={item.to} text={item.help} title={item.label} side="bottom">
                <NavLink
                  to={item.to}
                  end={'end' in item ? item.end : false}
                  className={cn(
                    'inline-flex shrink-0 items-center gap-1.5 rounded-full px-3 py-1.5 text-[14px] font-semibold leading-none whitespace-nowrap transition sm:px-3.5 sm:text-[15px]',
                    active
                      ? 'bg-[#111827] text-white shadow-sm dark:bg-white dark:text-[#111827]'
                      : 'text-[#4b5563] hover:bg-white/80 hover:text-[#111827] dark:text-text-secondary dark:hover:bg-bg-base dark:hover:text-text-primary',
                  )}
                >
                  <item.icon className="size-4 shrink-0 opacity-90" />
                  <span>{item.label}</span>
                </NavLink>
              </Explain>
            )
          })}
        </nav>

        <div className="ml-2 flex shrink-0 items-center gap-1 sm:ml-auto">
          <Explain text={helpText.nav.helpMode} title="说明模式" side="bottom">
            <Button
              variant={enabled ? 'default' : 'ghost'}
              size="icon"
              className="hidden size-9 rounded-full sm:inline-flex"
              onClick={toggle}
              title={enabled ? '关闭说明模式' : '说明模式'}
            >
              <CircleHelp className="size-4" />
            </Button>
          </Explain>
          <Explain text={helpText.nav.theme} title="外观" side="bottom">
            <Button
              variant="ghost"
              size="icon"
              className="size-9 rounded-full"
              onClick={toggleTheme}
              title="切换主题"
            >
              {theme === 'light' ? <Moon className="size-4" /> : <Sun className="size-4" />}
            </Button>
          </Explain>
        </div>
      </header>

      <HelpModeBanner />
      <main className="min-w-0 flex-1 overflow-hidden">
        <Outlet />
      </main>
    </div>
  )
}
