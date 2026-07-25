import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Bot, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { api } from '@/api'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/empty-state'
import { useAssistants, useKnowledgeBase, useKnowledgeBases, queryKeys } from '@/hooks/use-knowledge-request'
import { AssistantSettings } from '@/pages/assistants'
import {
  findAssistantForKnowledgeBase,
  GENERIC_TEMPLATE_ASSISTANT_ID,
} from '@/lib/assistants'

export default function KbAssistantPage({ mode }: { mode: 'chat' | 'workflow' }) {
  const { id = '' } = useParams()
  const client = useQueryClient()
  const { data: kb } = useKnowledgeBase(id)
  const { data: knowledgeBases = [] } = useKnowledgeBases()
  const { data: assistants = [], isLoading } = useAssistants()
  const [creating, setCreating] = useState(false)

  const assistant = findAssistantForKnowledgeBase(assistants, id)

  const ensureAssistant = async () => {
    if (!id || !kb) return
    setCreating(true)
    try {
      await api.createAssistant({
        name: `${kb.name}审查`,
        description: `绑定知识库「${kb.name}」的审查配置（由通用模板复制）`,
        knowledge_base_id: id,
      })
      await client.invalidateQueries({ queryKey: queryKeys.assistants })
      toast.success('已为本库创建审查配置')
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setCreating(false)
    }
  }

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center text-[15px] text-text-secondary">
        加载中…
      </div>
    )
  }

  if (!assistant) {
    return (
      <div className="flex h-full flex-col items-center justify-center px-6">
        <EmptyState
          icon={<Bot />}
          title="本库还没有审查配置"
          description="将从「通用审查模板」复制一份审查配置，并自动绑定当前知识库。模板本身可在系统设置中编辑。"
          actionLabel={creating ? '创建中…' : '创建审查配置'}
          onAction={() => void ensureAssistant()}
        />
        {creating && <Loader2 className="mt-4 size-5 animate-spin text-text-secondary" />}
        <Button variant="link" className="mt-2 text-[14px]" asChild>
          <Link to="/settings/assistant-template">查看通用模板</Link>
        </Button>
      </div>
    )
  }

  if (assistant.id === GENERIC_TEMPLATE_ASSISTANT_ID) {
    return (
      <div className="flex h-full items-center justify-center text-[15px] text-text-secondary">
        通用模板请到系统设置中编辑。
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-white md:-mx-2">
      <AssistantSettings
        assistant={assistant}
        knowledgeBases={knowledgeBases}
        lockedKnowledgeBaseId={id}
        initialTab={mode}
        embedded
        onChanged={() => client.invalidateQueries({ queryKey: queryKeys.assistants })}
      />
    </div>
  )
}
