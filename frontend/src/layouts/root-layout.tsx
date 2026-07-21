import { NavLink, Outlet } from 'react-router-dom'
import {
  Bot,
  CircleHelp,
  FolderOpen,
  History,
  Moon,
  Settings,
  Sun,
} from 'lucide-react'
import { Explain, HelpModeBanner } from '@/components/explain'
import { useHelpMode } from '@/components/help-mode'
import { useTheme } from '@/components/theme-provider'
import { Button } from '@/components/ui/button'
import { helpText } from '@/lib/help-text'
import { cn } from '@/lib/utils'

const NAV = [
  { to: '/', label: '知识库', icon: FolderOpen, end: true, help: helpText.nav.knowledge },
  { to: '/assistants', label: '审查助手', icon: Bot, help: helpText.nav.assistants },
  { to: '/runs', label: '运行记录', icon: History, help: helpText.nav.runs },
  { to: '/settings', label: '设置', icon: Settings, help: helpText.nav.settings },
] as const

export function RootLayout() {
  const { theme, toggleTheme } = useTheme()
  const { enabled, toggle } = useHelpMode()

  return (
    <div className="flex h-screen overflow-hidden bg-bg-canvas text-text-primary">
      <aside className="flex w-[72px] flex-col items-center border-r border-border-button bg-bg-base py-4 xl:w-56 xl:items-stretch xl:px-3">
        <div className="mb-6 flex items-center gap-3 px-1 xl:px-2">
          <div className="grid size-9 place-items-center rounded-lg bg-accent-primary text-xs font-bold text-white">
            CS
          </div>
          <div className="hidden xl:block">
            <div className="text-sm font-semibold">Chunk Studio</div>
            <div className="text-xs text-text-secondary">标准资料审查工作台</div>
          </div>
        </div>

        <nav className="flex flex-1 flex-col gap-2">
          {NAV.map(item => (
            <Explain key={item.to} text={item.help} title={item.label} side="right" className="w-full">
              <NavLink
                to={item.to}
                end={'end' in item ? item.end : false}
                className={({ isActive }) =>
                  cn(
                    'flex w-full items-center justify-center gap-2.5 rounded-lg px-2 py-2.5 text-sm text-text-secondary transition hover:bg-bg-card hover:text-text-primary xl:justify-start xl:px-3',
                    isActive && 'bg-bg-card font-medium text-text-primary',
                  )
                }
              >
                <item.icon className="size-5 shrink-0" />
                <span className="hidden xl:inline">{item.label}</span>
              </NavLink>
            </Explain>
          ))}
        </nav>

        <div className="mt-auto flex flex-col items-center gap-1 xl:items-stretch">
          <Explain text={helpText.nav.helpMode} title="说明模式" side="right" className="w-full">
            <Button
              variant={enabled ? 'default' : 'ghost'}
              size="sm"
              className="w-full justify-center xl:justify-start"
              onClick={toggle}
              title="说明模式"
            >
              <CircleHelp />
              <span className="hidden xl:inline">{enabled ? '说明模式开' : '说明模式'}</span>
            </Button>
          </Explain>
          <Explain text={helpText.nav.theme} title="外观" side="right" className="w-full">
            <Button variant="ghost" size="icon" className="xl:w-full xl:justify-start xl:px-3" onClick={toggleTheme} title="切换主题">
              {theme === 'light' ? <Moon /> : <Sun />}
              <span className="hidden xl:inline">外观</span>
            </Button>
          </Explain>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <HelpModeBanner />
        <main className="min-w-0 flex-1 overflow-hidden">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
