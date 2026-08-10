import { useMemo } from 'react'
import { Bot, LoaderCircle } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { AssistantSettings } from '@/pages/assistants'
import { useAssistants, useKnowledgeBases } from '@/hooks/use-knowledge-request'
import {
  DEFAULT_OIL_ASSISTANT_ID,
  findAssistantForKnowledgeBase,
  GENERIC_TEMPLATE_ASSISTANT_ID,
} from '@/lib/assistants'

export default function BusinessAnalyticsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const knowledgeBaseId = searchParams.get('knowledge_base_id') || ''
  const assistantsQuery = useAssistants()
  const knowledgeBasesQuery = useKnowledgeBases()
  const assistants = assistantsQuery.data
  const knowledgeBases = knowledgeBasesQuery.data || []
  const assistant = useMemo(() => {
    const availableAssistants = assistants || []
    if (knowledgeBaseId) {
      return findAssistantForKnowledgeBase(availableAssistants, knowledgeBaseId)
    }
    return availableAssistants.find(item => item.id === DEFAULT_OIL_ASSISTANT_ID)
      || availableAssistants.find(item => (
        item.id !== GENERIC_TEMPLATE_ASSISTANT_ID
        && item.status !== 'archived'
        && Boolean(item.active_version_id)
      ))
  }, [assistants, knowledgeBaseId])
  const activeKnowledgeBaseId = knowledgeBaseId || assistant?.knowledge_bases[0]?.id || ''

  if (assistantsQuery.isLoading || knowledgeBasesQuery.isLoading) {
    return (
      <div className="grid h-full place-items-center bg-[#f8fafc]">
        <LoaderCircle className="size-7 animate-spin text-[#13c2c2]" />
      </div>
    )
  }

  if (!assistant) {
    return (
      <main className="grid h-full place-items-center bg-[#f8fafc] px-6">
        <div className="max-w-md rounded-2xl border border-[#e5e7eb] bg-white p-8 text-center shadow-sm">
          <Bot className="mx-auto size-10 text-[#13c2c2]" />
          <h1 className="mt-4 text-xl font-semibold">还没有可用的智能问答助手</h1>
          <p className="mt-2 text-sm leading-relaxed text-text-secondary">
            请先在知识库中绑定一个审查配置。之后这里会同时支持文档问答、业务统计、SQL 和图表。
          </p>
        </div>
      </main>
    )
  }

  return (
    <AssistantSettings
      assistant={assistant}
      knowledgeBases={knowledgeBases}
      onChanged={() => {
        void assistantsQuery.refetch()
        void knowledgeBasesQuery.refetch()
      }}
      initialTab="chat"
      embedded
      title="智能问答"
      chatSidebar="left"
      chatKnowledgeBaseId={activeKnowledgeBaseId}
      onChatKnowledgeBaseChange={nextId => {
        if (nextId === activeKnowledgeBaseId) return
        setSearchParams({ knowledge_base_id: nextId })
      }}
      hideKnowledgePicker
    />
  )
}
