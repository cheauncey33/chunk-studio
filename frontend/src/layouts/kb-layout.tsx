import { NavLink, Outlet, useParams } from 'react-router-dom'
import {
  Bot,
  FolderOpen,
  MessageSquare,
  Settings,
  TextSearch,
  Workflow,
} from 'lucide-react'
import { Explain } from '@/components/explain'
import { useKnowledgeBase } from '@/hooks/use-knowledge-request'
import { helpText } from '@/lib/help-text'
import { cn, formatDate } from '@/lib/utils'

const ITEMS = [
  { to: 'files', label: '文件概览', icon: FolderOpen, help: helpText.kbNav.files },
  { to: 'retrieval', label: '检索测试', icon: TextSearch, help: helpText.kbNav.retrieval },
  { to: 'chat', label: '智能问答', icon: MessageSquare, help: helpText.kbNav.chat },
  { to: 'workflow', label: '审查配置', icon: Workflow, help: helpText.kbNav.workflow },
  { to: 'settings', label: '配置', icon: Settings, help: helpText.kbNav.settings },
] as const

export function KnowledgeLayout() {
  const { id = '' } = useParams()
  const { data: kb, isLoading } = useKnowledgeBase(id)

  return (
    <div className="grid size-full grid-cols-[auto_1fr] overflow-hidden px-6 pt-6">
      <aside className="flex w-64 flex-col">
        <header className="grid grid-cols-[auto_1fr] gap-x-3 px-3 pb-4">
          <div className="grid size-14 place-items-center rounded-xl bg-bg-accent text-accent-primary">
            <FolderOpen className="size-6" />
          </div>
          <div className="min-w-0 self-center">
            <Explain text={helpText.datasets.page} title="当前知识库">
              <h3 className="truncate text-[17px] font-semibold">
                {isLoading ? '加载中…' : kb?.name || '知识库'}
              </h3>
            </Explain>
            <div className="mt-1 space-y-0.5 text-[15px] text-text-secondary">
              <div className="flex justify-between gap-2">
                <span>{kb?.file_count ?? 0} 个文件</span>
                <span>{kb?.chunk_count ?? 0} 段内容</span>
              </div>
              <div>更新 {kb ? formatDate(kb.updated_at) : '—'}</div>
            </div>
          </div>
        </header>

        <nav className="overflow-y-auto px-3 pb-5 pt-1">
          <ul className="space-y-1.5">
            {ITEMS.map(item => (
              <li key={item.to}>
                <NavLink
                  to={`/kb/${id}/${item.to}`}
                  title={item.help}
                  className={({ isActive }) =>
                    cn(
                      'flex h-11 w-full items-center gap-2.5 rounded-xl px-3 text-[16px] text-text-secondary transition hover:bg-bg-card hover:text-text-primary',
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
          <p className="mt-4 px-3 text-[12px] leading-relaxed text-text-secondary">
            <Bot className="mr-1 inline size-3.5 align-text-bottom" />
            智能问答与审查配置属于本库；通用模板在系统设置。
          </p>
        </nav>
      </aside>

      <div className="min-w-0 overflow-auto pr-2 pb-6">
        <Outlet />
      </div>
    </div>
  )
}
