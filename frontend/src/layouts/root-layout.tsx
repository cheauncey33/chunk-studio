import { NavLink, Outlet } from 'react-router-dom'
import {
  Bot,
  FolderOpen,
  History,
  Moon,
  Settings,
  Sun,
} from 'lucide-react'
import { useTheme } from '@/components/theme-provider'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

const NAV = [
  { to: '/', label: '知识库', icon: FolderOpen, end: true },
  { to: '/assistants', label: '审查助手', icon: Bot },
  { to: '/runs', label: '运行记录', icon: History },
  { to: '/settings', label: '设置', icon: Settings },
] as const

export function RootLayout() {
  const { theme, toggleTheme } = useTheme()

  return (
    <div className="flex h-screen overflow-hidden bg-bg-canvas text-text-primary">
      <aside className="flex w-[72px] flex-col items-center border-r border-border-button bg-bg-base py-4 xl:w-56 xl:items-stretch xl:px-3">
        <div className="mb-6 flex items-center gap-3 px-1 xl:px-2">
          <div className="grid size-9 place-items-center rounded-lg bg-accent-primary text-xs font-bold text-white">
            CS
          </div>
          <div className="hidden xl:block">
            <div className="text-sm font-semibold">Chunk Studio</div>
            <div className="text-xs text-text-secondary">PDF 知识库工作台</div>
          </div>
        </div>

        <nav className="flex flex-1 flex-col gap-2">
          {NAV.map(item => (
            <NavLink
              key={item.to}
              to={item.to}
              end={'end' in item ? item.end : false}
              className={({ isActive }) =>
                cn(
                  'flex items-center justify-center gap-2.5 rounded-lg px-2 py-2.5 text-sm text-text-secondary transition hover:bg-bg-card hover:text-text-primary xl:justify-start xl:px-3',
                  isActive && 'bg-bg-card font-medium text-text-primary',
                )
              }
            >
              <item.icon className="size-5 shrink-0" />
              <span className="hidden xl:inline">{item.label}</span>
            </NavLink>
          ))}
        </nav>

        <Button variant="ghost" size="icon" className="mt-auto" onClick={toggleTheme} title="切换主题">
          {theme === 'light' ? <Moon /> : <Sun />}
        </Button>
      </aside>

      <main className="min-w-0 flex-1 overflow-hidden">
        <Outlet />
      </main>
    </div>
  )
}
