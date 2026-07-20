import { NavLink, Outlet, useParams } from 'react-router-dom'
import {
  FolderOpen,
  Settings,
  Sparkles,
  TextSearch,
  Layers,
} from 'lucide-react'
import { useKnowledgeBase } from '@/hooks/use-knowledge-request'
import { cn, formatDate } from '@/lib/utils'

const ITEMS = [
  { to: 'files', label: '文件', icon: FolderOpen },
  { to: 'chunks', label: '切片', icon: Layers },
  { to: 'retrieval', label: '检索测试', icon: TextSearch },
  { to: 'metadata', label: '元数据审核', icon: Sparkles },
  { to: 'settings', label: '设置', icon: Settings },
] as const

export function KnowledgeLayout() {
  const { id = '' } = useParams()
  const { data: kb, isLoading } = useKnowledgeBase(id)

  return (
    <div className="grid size-full grid-cols-[auto_1fr] overflow-hidden pt-3">
      <aside className="flex w-64 flex-col">
        <header className="grid grid-cols-[auto_1fr] gap-x-3 px-5 pb-4">
          <div className="grid size-16 place-items-center rounded-xl bg-bg-accent text-accent-primary">
            <FolderOpen className="size-7" />
          </div>
          <div className="min-w-0 self-center">
            <h3 className="truncate text-lg font-semibold">
              {isLoading ? '加载中…' : kb?.name || '知识库'}
            </h3>
            <div className="mt-1 space-y-0.5 text-xs text-text-secondary">
              <div className="flex justify-between gap-2">
                <span>{kb?.file_count ?? 0} 个文件</span>
                <span>{kb?.chunk_count ?? 0} 切片</span>
              </div>
              <div>更新 {kb ? formatDate(kb.updated_at) : '—'}</div>
            </div>
          </div>
        </header>

        <nav className="overflow-y-auto px-5 pb-5 pt-1">
          <ul className="space-y-2">
            {ITEMS.map(item => (
              <li key={item.to}>
                <NavLink
                  to={`/kb/${id}/${item.to}`}
                  className={({ isActive }) =>
                    cn(
                      'flex h-10 items-center gap-2.5 rounded-lg px-3 text-base text-text-secondary transition hover:bg-bg-card hover:text-text-primary',
                      isActive && 'bg-bg-card font-medium text-text-primary',
                    )
                  }
                >
                  <item.icon className="size-4" />
                  <span>{item.label}</span>
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      </aside>

      <div className="min-w-0 overflow-auto pr-5 pb-5">
        <Outlet />
      </div>
    </div>
  )
}
