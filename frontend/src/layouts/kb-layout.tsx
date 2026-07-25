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
  { to: 'settings', label: '知识库设置', icon: Settings, help: helpText.kbNav.settings },
] as const

export function KnowledgeLayout() {
  const { id = '' } = useParams()
  const { data: kb, isLoading } = useKnowledgeBase(id)

  return (
    <div className="grid size-full grid-rows-[auto_minmax(0,1fr)] overflow-hidden px-3 pt-3 md:grid-cols-[auto_1fr] md:grid-rows-1 md:px-6 md:pt-6">
      <aside className="flex w-full min-w-0 flex-col border-b border-border-button md:w-64 md:border-b-0">
        <header className="grid grid-cols-[auto_1fr] gap-x-3 px-2 pb-2 md:px-3 md:pb-4">
          <div className="grid size-11 place-items-center rounded-xl bg-bg-accent text-accent-primary md:size-14">
            <FolderOpen className="size-6" />
          </div>
          <div className="min-w-0 self-center">
            <Explain text={helpText.datasets.page} title="当前知识库">
              <h3 className="truncate text-[17px] font-semibold">
                {isLoading ? '加载中…' : kb?.name || '知识库'}
              </h3>
            </Explain>
            <div className="mt-1 flex flex-wrap gap-x-3 text-[13px] text-text-secondary md:block md:space-y-0.5 md:text-[15px]">
              <div className="flex gap-3 md:justify-between md:gap-2">
                <span>{kb?.file_count ?? 0} 个文件</span>
                <span>{kb?.chunk_count ?? 0} 段内容</span>
              </div>
              <div className="hidden md:block">更新 {kb ? formatDate(kb.updated_at) : '—'}</div>
            </div>
          </div>
        </header>

        <nav className="overflow-x-auto px-1 pb-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden md:overflow-y-auto md:px-3 md:pb-5 md:pt-1">
          <ul className="flex min-w-max gap-1 md:block md:min-w-0 md:space-y-1.5">
            {ITEMS.map(item => (
              <li key={item.to}>
                <NavLink
                  to={`/kb/${id}/${item.to}`}
                  title={item.help}
                  className={({ isActive }) =>
                    cn(
                      'flex h-10 w-full items-center gap-2 rounded-xl px-3 text-[14px] text-text-secondary transition hover:bg-bg-card hover:text-text-primary md:h-11 md:gap-2.5 md:text-[16px]',
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
          <p className="mt-4 hidden px-3 text-[12px] leading-relaxed text-text-secondary md:block">
            <Bot className="mr-1 inline size-3.5 align-text-bottom" />
            智能问答与审查配置属于本库；通用模板在系统设置。
          </p>
        </nav>
      </aside>

      <div className="min-h-0 min-w-0 overflow-auto pb-3 pt-3 md:pb-6 md:pr-2 md:pt-0">
        <Outlet />
      </div>
    </div>
  )
}
