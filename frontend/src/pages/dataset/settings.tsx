import { useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2, Upload } from 'lucide-react'
import { toast } from 'sonner'
import { api, type FewShotRules, type ManualKnowledgeRules } from '@/api'
import {
  ChunkRuleFields,
  DEFAULT_CHUNK_RULES,
  normalizeChunkRules,
  type ChunkRuleConfig,
} from '@/components/chunk-rule-fields'
import { Button } from '@/components/ui/button'
import { Input, Label, Textarea } from '@/components/ui/input'
import {
  queryKeys,
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

export default function DatasetSettingsPage() {
  const { id = '' } = useParams()
  const client = useQueryClient()
  const { data: knowledgeBase } = useKnowledgeBase(id)
  const updateKb = useUpdateKnowledgeBase(id)
  const namingRef = useRef<HTMLInputElement>(null)

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [chunkRules, setChunkRules] = useState<ChunkRuleConfig>(DEFAULT_CHUNK_RULES)
  const [namingFileId, setNamingFileId] = useState('')
  const [namingFileName, setNamingFileName] = useState('')
  const [namingUploading, setNamingUploading] = useState(false)
  const [manualRules, setManualRules] = useState<ManualRule[]>([])
  const [manualMeta, setManualMeta] = useState({ version: 1, status: 'draft' })
  const [fewShots, setFewShots] = useState<FewShotItem[]>([])

  useEffect(() => {
    if (!knowledgeBase) return
    setName(knowledgeBase.name)
    setDescription(knowledgeBase.description)
    setChunkRules(normalizeChunkRules(knowledgeBase.parser_config || {}))
    setNamingFileId(knowledgeBase.default_naming_file_id || '')
    setNamingFileName(knowledgeBase.default_naming_file_name || '')
    const rulesPayload = knowledgeBase.manual_rules || {}
    setManualRules(Array.isArray(rulesPayload.rules) ? [...rulesPayload.rules] : [])
    setManualMeta({
      version: Number(rulesPayload.version ?? 1),
      status: String(rulesPayload.status || 'draft'),
    })
    const few = knowledgeBase.few_shot_rules || {}
    setFewShots(Array.isArray(few.items) ? [...few.items] : [])
  }, [knowledgeBase])

  const uploadNaming = async (file?: File) => {
    if (!file || !id) return
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      toast.error('请上传 PDF')
      return
    }
    setNamingUploading(true)
    try {
      const uploaded = await api.uploadFile(
        file,
        { doc_role: 'naming', doc_type: 'naming' },
        id,
      )
      setNamingFileId(uploaded.id)
      setNamingFileName(uploaded.name)
      await client.invalidateQueries({ queryKey: queryKeys.knowledgeBase(id) })
      await client.invalidateQueries({ queryKey: queryKeys.knowledgeBases })
      toast.success('命名规则 PDF 已上传并设为库属性')
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setNamingUploading(false)
      if (namingRef.current) namingRef.current.value = ''
    }
  }

  const clearNaming = () => {
    setNamingFileId('')
    setNamingFileName('')
  }

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
        name,
        description,
        parser_config: chunkRules as unknown as Record<string, unknown>,
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
    <div className="space-y-5 pt-2">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-[24px] font-semibold tracking-tight">知识库设置</h1>
          <p className="mt-1.5 max-w-3xl text-[15px] leading-relaxed text-text-secondary">
            知识库属性：名称、切片规则，以及命名 PDF / 约定 / 示例。检索阈值与返回条数在审查助手里配置。
          </p>
        </div>
        <Button disabled={updateKb.isPending} onClick={save}>
          {updateKb.isPending ? '保存中…' : '保存'}
        </Button>
      </div>

      <div className="grid gap-5 xl:grid-cols-2">
        <div className="space-y-5">
          <section className="space-y-4 rounded-xl border border-border-button bg-bg-base p-5">
            <h2 className="text-[16px] font-semibold">基本信息</h2>
            <div className="space-y-2">
              <Label htmlFor="kb-config-name">名称</Label>
              <Input id="kb-config-name" value={name} onChange={e => setName(e.target.value)} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="kb-config-desc">说明</Label>
              <Textarea
                id="kb-config-desc"
                value={description}
                onChange={e => setDescription(e.target.value)}
              />
            </div>
          </section>

          <section className="space-y-4 rounded-xl border border-border-button bg-bg-base p-5">
            <h2 className="text-[16px] font-semibold">默认切片规则</h2>
            <p className="text-[14px] text-text-secondary">
              决定本库 PDF 如何切成可检索片段，属于语料加工属性。
            </p>
            <ChunkRuleFields value={chunkRules} onChange={setChunkRules} />
          </section>
        </div>

        <div className="space-y-5">
          <section className="space-y-3 rounded-xl border border-border-button bg-bg-base p-5">
            <h2 className="text-[16px] font-semibold">审查补充</h2>
            <p className="text-[14px] text-text-secondary">
              型号命名规则是库属性，不参与检索。补充规则会全量注入审查助手的「对照标准判定」节点，由判定提示词按 allowed_use 约束使用。
            </p>
              <div className="space-y-2">
                <Label>型号命名规则 PDF</Label>
                <div className="flex flex-wrap items-center gap-2">
                  <input
                    ref={namingRef}
                    hidden
                    type="file"
                    accept="application/pdf"
                    onChange={e => void uploadNaming(e.target.files?.[0])}
                  />
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={namingUploading}
                    onClick={() => namingRef.current?.click()}
                  >
                    <Upload className="size-3.5" />
                    {namingUploading ? '上传中…' : namingFileId ? '更换' : '上传'}
                  </Button>
                  {namingFileId && (
                    <Button type="button" variant="ghost" size="sm" onClick={clearNaming}>
                      清除
                    </Button>
                  )}
                </div>
                {namingFileId ? (
                  <a
                    href={api.fileContentUrl(namingFileId)}
                    target="_blank"
                    rel="noreferrer"
                    title="在新标签页打开 PDF"
                    className="inline-flex max-w-full items-center rounded-lg border border-accent-primary/25 bg-bg-accent px-3 py-2 text-[15px] font-medium text-accent-primary underline-offset-2 transition hover:border-accent-primary/45 hover:underline"
                  >
                    <span className="truncate">
                      {namingFileName || namingFileId}
                    </span>
                  </a>
                ) : (
                  <p className="text-[15px] text-text-secondary">尚未设置</p>
                )}
              </div>
          </section>

          <section className="space-y-3 rounded-xl border border-border-button bg-bg-base p-5">
            <div className="flex items-center justify-between gap-2">
              <div>
                <Label>补充规则</Label>
                <p className="mt-0.5 text-[13px] text-text-secondary">
                  写入判定节点上下文。规则宜写清适用条件与用法边界（可参考总损耗公式那条）。
                </p>
              </div>
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
                <div
                  key={`${item.id}-${index}`}
                  className="space-y-2 rounded-lg border border-border-button p-3"
                >
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
      </div>
    </div>
  )
}
