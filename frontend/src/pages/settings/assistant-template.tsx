import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Bot, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { api } from '@/api'
import { Explain } from '@/components/explain'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/input'
import { SearchableSelect } from '@/components/searchable-select'
import { useAssistants, useKnowledgeBases, queryKeys } from '@/hooks/use-knowledge-request'
import { AssistantSettings } from '@/pages/assistants'
import {
  DEFAULT_OIL_ASSISTANT_ID,
  GENERIC_TEMPLATE_ASSISTANT_ID,
} from '@/lib/assistants'
import { helpText } from '@/lib/help-text'

export default function AssistantTemplateSettingsPage() {
  const client = useQueryClient()
  const { data: assistants = [], isLoading } = useAssistants()
  const { data: knowledgeBases = [] } = useKnowledgeBases()
  const template = assistants.find(item => item.id === GENERIC_TEMPLATE_ASSISTANT_ID)

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center gap-2 text-[15px] text-text-secondary">
        <Loader2 className="size-4 animate-spin" />
        加载中…
      </div>
    )
  }

  if (!template) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 px-8">
        <p className="text-[15px] text-text-secondary">未找到通用审查模板种子。</p>
        <Button asChild variant="outline">
          <Link to="/settings">返回设置</Link>
        </Button>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex shrink-0 items-center justify-between gap-3 border-b border-border-button px-8 py-4">
        <div>
          <Explain text={helpText.settings.assistantTemplate} title="通用审查模板">
            <h1 className="text-[18px] font-semibold tracking-tight">通用审查模板</h1>
          </Explain>
          <p className="mt-1 text-[15px] text-text-secondary">
            新建知识库审查配置时会复制此模板。改这里会影响后续新建，不会自动改写已有品类助手。
          </p>
        </div>
        <Button asChild variant="outline">
          <Link to="/settings">返回设置</Link>
        </Button>
      </div>
      <div className="min-h-0 flex-1 overflow-hidden">
        <AssistantSettings
          assistant={template}
          knowledgeBases={knowledgeBases}
          initialTab="workflow"
          embedded
          hideKnowledgePicker
          onChanged={() => client.invalidateQueries({ queryKey: queryKeys.assistants })}
        />
      </div>
    </div>
  )
}

export function AuditDefaultsCard() {
  const { data: assistants = [], isLoading: assistantsLoading, isError: assistantsError } = useAssistants()
  const [defaultAssistantId, setDefaultAssistantId] = useState(DEFAULT_OIL_ASSISTANT_ID)
  const [saving, setSaving] = useState(false)
  const [settingsReady, setSettingsReady] = useState(false)
  const [settingsError, setSettingsError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .getSettings()
      .then(payload => {
        if (cancelled) return
        const value = String(payload.settings['audit.default_assistant_id'] || '').trim()
        setDefaultAssistantId(value || DEFAULT_OIL_ASSISTANT_ID)
        setSettingsError(null)
      })
      .catch(err => {
        if (cancelled) return
        setSettingsError((err as Error).message || '无法读取系统设置（后端可能未启动）')
      })
      .finally(() => {
        if (!cancelled) setSettingsReady(true)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const options = assistants
    .filter(item => item.id !== GENERIC_TEMPLATE_ASSISTANT_ID)
    .map(item => ({
      value: item.id,
      label: item.name,
      hint: item.knowledge_bases.map(kb => kb.name).join('、') || '未绑库',
      group: '审查助手',
    }))

  const save = async () => {
    setSaving(true)
    try {
      await api.updateSettings({ 'audit.default_assistant_id': defaultAssistantId })
      toast.success('已保存路由兜底助手')
      setSettingsError(null)
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const placeholder = !settingsReady || assistantsLoading
    ? '加载中…'
    : assistantsError || settingsError
      ? '加载失败，请确认后端已启动'
      : options.length
        ? '请选择'
        : '暂无可用助手'

  return (
    <Card className="mb-5 border-border-button bg-bg-base">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-[17px]">
          <Bot className="size-4" />
          审查路由兜底
        </CardTitle>
        <CardDescription className="text-[15px]">
          工作台会按报告内容自动路由到知识库对应助手；此处仅在路由失败或结果无效时生效。通用模板供新建知识库复制。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Explain text={helpText.settings.defaultAssistant} title="路由兜底助手">
            <Label>路由兜底助手</Label>
          </Explain>
          <SearchableSelect
            value={settingsReady && !assistantsLoading ? defaultAssistantId : ''}
            options={options}
            placeholder={placeholder}
            onChange={setDefaultAssistantId}
          />
          {(assistantsError || settingsError) && (
            <p className="text-[13px] text-state-error">
              {settingsError || '无法加载助手列表。请确认后端 http://127.0.0.1:8000 已启动，然后刷新页面。'}
            </p>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            disabled={!settingsReady || assistantsLoading || Boolean(assistantsError || settingsError) || saving}
            onClick={() => void save()}
          >
            {saving ? '保存中…' : '保存兜底助手'}
          </Button>
          <Button asChild variant="outline">
            <Link to="/settings/assistant-template">编辑通用审查模板</Link>
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
