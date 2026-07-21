import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Plus, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import type { FewShotRules, ManualKnowledgeRules } from '@/api'
import { Button } from '@/components/ui/button'
import { Input, Label, Textarea } from '@/components/ui/input'
import {
  useKbFiles,
  useKnowledgeBase,
  useUpdateKnowledgeBase,
} from '@/hooks/use-knowledge-request'

type ManualRule = NonNullable<ManualKnowledgeRules['rules']>[number]
type FewShotItem = NonNullable<FewShotRules['items']>[number]

const FLOW_NODE_OPTIONS = [
  { value: '', label: '不限节点' },
  { value: 'report_parameters', label: '提取报告参数' },
  { value: 'test_items', label: '提取检测项目' },
  { value: 'model_decode', label: '解析型号规则' },
  { value: 'query_planner', label: '规划检索问题' },
  { value: 'audit_judge', label: '对照标准判定' },
]

function emptyManualRule(): ManualRule {
  return {
    rule_id: `rule_${Date.now().toString(36)}`,
    rule_text: '',
    domain: 'transformer_standard_value_audit',
    rule_type: 'manual',
  }
}

function emptyFewShot(): FewShotItem {
  return {
    id: `fs_${Date.now().toString(36)}`,
    title: '',
    node: '',
    input: '',
    output: '',
    note: '',
  }
}

export default function DatasetRulesPage() {
  const { id = '' } = useParams()
  const { data: knowledgeBase } = useKnowledgeBase(id)
  const { data: files = [] } = useKbFiles(id)
  const updateKb = useUpdateKnowledgeBase(id)

  const [namingFileId, setNamingFileId] = useState('')
  const [manualRules, setManualRules] = useState<ManualRule[]>([])
  const [manualMeta, setManualMeta] = useState({ version: 1, status: 'draft' })
  const [fewShots, setFewShots] = useState<FewShotItem[]>([])

  const readyFiles = useMemo(
    () => files.filter(file => file.parse_ready || file.parse_status === 'done'),
    [files],
  )

  useEffect(() => {
    if (!knowledgeBase) return
    setNamingFileId(knowledgeBase.default_naming_file_id || '')
    const rulesPayload = knowledgeBase.manual_rules || {}
    setManualRules(Array.isArray(rulesPayload.rules) ? [...rulesPayload.rules] : [])
    setManualMeta({
      version: Number(rulesPayload.version ?? 1),
      status: String(rulesPayload.status || 'draft'),
    })
    const few = knowledgeBase.few_shot_rules || {}
    setFewShots(Array.isArray(few.items) ? [...few.items] : [])
  }, [knowledgeBase])

  const save = async () => {
    const cleanedRules = manualRules
      .map((rule, index) => {
        const text = String(rule.rule_text || '').trim()
        const ruleId = String(rule.rule_id || '').trim() || `rule_${index + 1}`
        return {
          ...rule,
          rule_id: ruleId,
          rule_text: text,
          domain: String(rule.domain || '').trim() || 'transformer_standard_value_audit',
          rule_type: String(rule.rule_type || 'manual'),
        }
      })
      .filter(rule => rule.rule_text)

    const cleanedFew = fewShots
      .map(item => ({
        ...item,
        id: String(item.id || '').trim(),
        title: String(item.title || '').trim(),
        node: String(item.node || '').trim() || undefined,
        input: String(item.input || ''),
        output: String(item.output || ''),
        note: String(item.note || '').trim() || undefined,
      }))
      .filter(item => item.id && (item.input || item.output || item.title))

    try {
      await updateKb.mutateAsync({
        manual_rules: {
          version: manualMeta.version,
          scope: 'knowledge_base_manual_rules',
          status: manualMeta.status,
          rules: cleanedRules,
        },
        few_shot_rules: {
          version: 1,
          items: cleanedFew,
        },
        ...(namingFileId
          ? { default_naming_file_id: namingFileId }
          : { clear_default_naming_file: true }),
      })
      toast.success('已保存')
    } catch (err) {
      toast.error((err as Error).message)
    }
  }

  return (
    <div className="max-w-3xl space-y-6 pt-2">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold">审查补充</h1>
        <Button disabled={updateKb.isPending} onClick={save}>
          {updateKb.isPending ? '保存中…' : '保存'}
        </Button>
      </div>

      <section className="space-y-3 rounded-xl border border-border-button bg-bg-base p-5">
        <Label htmlFor="kb-naming-file">型号命名规则 PDF</Label>
        <select
          id="kb-naming-file"
          className="flex h-9 w-full rounded-md border border-border-button bg-bg-base px-3 text-sm"
          value={namingFileId}
          onChange={e => setNamingFileId(e.target.value)}
        >
          <option value="">不指定</option>
          {readyFiles.map(file => (
            <option key={file.id} value={file.id}>
              {file.name}
            </option>
          ))}
        </select>
      </section>

      <section className="space-y-3 rounded-xl border border-border-button bg-bg-base p-5">
        <div className="flex items-center justify-between gap-2">
          <Label>补充规则</Label>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => setManualRules(prev => [...prev, emptyManualRule()])}
          >
            <Plus />
            添加
          </Button>
        </div>
        {!manualRules.length && (
          <p className="text-sm text-text-secondary">暂无规则</p>
        )}
        {manualRules.map((rule, index) => (
          <div key={`${rule.rule_id}-${index}`} className="flex items-start gap-2">
            <Textarea
              rows={3}
              placeholder={`规则 ${index + 1}，例如：总损耗 = 空载损耗 + 负载损耗`}
              value={String(rule.rule_text || '')}
              onChange={e => {
                const value = e.target.value
                setManualRules(prev =>
                  prev.map((item, i) => (i === index ? { ...item, rule_text: value } : item)),
                )
              }}
            />
            <Button
              type="button"
              size="icon"
              variant="ghost"
              title="删除"
              onClick={() => setManualRules(prev => prev.filter((_, i) => i !== index))}
            >
              <Trash2 />
            </Button>
          </div>
        ))}
      </section>

      <details className="rounded-xl border border-border-button bg-bg-base p-5">
        <summary className="cursor-pointer select-none text-sm font-medium">高级示例</summary>
        <div className="mt-4 space-y-3">
          <div className="flex justify-end">
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => setFewShots(prev => [...prev, emptyFewShot()])}
            >
              <Plus />
              添加
            </Button>
          </div>
          {fewShots.map((item, index) => (
            <div key={`${item.id}-${index}`} className="space-y-2 rounded-lg border border-border-button p-3">
              <div className="flex items-start gap-2">
                <div className="grid flex-1 gap-2 sm:grid-cols-2">
                  <Input
                    placeholder="标题"
                    value={item.title || ''}
                    onChange={e => {
                      const value = e.target.value
                      setFewShots(prev =>
                        prev.map((row, i) => (i === index ? { ...row, title: value } : row)),
                      )
                    }}
                  />
                  <select
                    className="flex h-9 w-full rounded-md border border-border-button bg-bg-base px-3 text-sm"
                    value={item.node || ''}
                    onChange={e => {
                      const value = e.target.value
                      setFewShots(prev =>
                        prev.map((row, i) => (i === index ? { ...row, node: value } : row)),
                      )
                    }}
                  >
                    {FLOW_NODE_OPTIONS.map(opt => (
                      <option key={opt.value || 'any'} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                </div>
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  title="删除"
                  onClick={() => setFewShots(prev => prev.filter((_, i) => i !== index))}
                >
                  <Trash2 />
                </Button>
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                <Textarea
                  rows={3}
                  placeholder="输入"
                  value={item.input || ''}
                  onChange={e => {
                    const value = e.target.value
                    setFewShots(prev =>
                      prev.map((row, i) => (i === index ? { ...row, input: value } : row)),
                    )
                  }}
                />
                <Textarea
                  rows={3}
                  placeholder="期望输出"
                  value={item.output || ''}
                  onChange={e => {
                    const value = e.target.value
                    setFewShots(prev =>
                      prev.map((row, i) => (i === index ? { ...row, output: value } : row)),
                    )
                  }}
                />
              </div>
            </div>
          ))}
        </div>
      </details>
    </div>
  )
}
