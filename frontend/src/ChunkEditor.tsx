import { useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import { api, type Chunk, type ChunkStatus, type FieldConfig } from '@/api'
import {
  acceptedPathForField,
  getByPath,
  getBusinessMetadata,
  isAutoChunk,
  setByPath,
  storagePathForField,
  suggestionValue,
} from '@/chunkSchema'
import {
  formatGeneratedAt,
  parseLlmSuggestion,
  shortHash,
  suggestionItems,
  textSha256,
} from '@/llmMetadata'
import { Explain } from '@/components/explain'
import { Badge, Input, Label, Textarea } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { helpText } from '@/lib/help-text'
import { renderChunkText } from '@/lib/render-chunk-text'
import { cn } from '@/lib/utils'

interface Props {
  chunk: Chunk | null
  fields: FieldConfig[]
  onSaved: (c: Chunk) => void
  onDelete: (id: string) => void
  onQueued: () => void
}

type TextMode = 'source' | 'preview'
type SuggestionFreshness = 'checking' | 'current' | 'changed' | 'unknown'
type EditorTab = 'content' | 'metadata' | 'review' | 'llm'

const SUGGESTION_LABELS: Record<string, string> = {
  keywords: '关键词',
  questions: '可回答问题',
}

const STATUS_VARIANT: Record<string, 'secondary' | 'success' | 'warning' | 'error'> = {
  pending: 'warning',
  reviewed: 'secondary',
  approved: 'success',
  rejected: 'error',
}

const STATUS_LABEL: Record<string, string> = {
  pending: '待处理',
  reviewed: '已复核',
  approved: '已批准',
  rejected: '已驳回',
}

export function ChunkEditor({ chunk, fields, onSaved, onDelete, onQueued }: Props) {
  const [text, setText] = useState('')
  const [meta, setMeta] = useState<Record<string, unknown>>({})
  const [saving, setSaving] = useState(false)
  const [statusBusy, setStatusBusy] = useState(false)
  const [ocring, setOcring] = useState(false)
  const [dirtyText, setDirtyText] = useState(false)
  const [dirtyMeta, setDirtyMeta] = useState(false)
  const [textMode, setTextMode] = useState<TextMode>('preview')
  const [tab, setTab] = useState<EditorTab>('content')
  const [suggestionFreshness, setSuggestionFreshness] = useState<SuggestionFreshness>('unknown')

  useEffect(() => {
    if (!chunk) return
    setText(chunk.text || '')
    setMeta({ ...getBusinessMetadata(chunk) })
    setDirtyText(false)
    setDirtyMeta(false)
    setTextMode('preview')
    setTab('content')
  }, [chunk?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!chunk || dirtyText) return
    setText(chunk.text || '')
  }, [chunk?.text, chunk?.updated_at, dirtyText]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let cancelled = false
    if (!chunk) {
      setSuggestionFreshness('unknown')
      return
    }
    const sourceHash = Object.values(chunk.metadata_llm || {})
      .map(parseLlmSuggestion)
      .find(item => item.sourceTextSha256)?.sourceTextSha256 || ''
    if (!sourceHash || !crypto.subtle) {
      setSuggestionFreshness('unknown')
      return
    }
    setSuggestionFreshness('checking')
    textSha256(chunk.text || '')
      .then(hash => { if (!cancelled) setSuggestionFreshness(hash === sourceHash ? 'current' : 'changed') })
      .catch(() => { if (!cancelled) setSuggestionFreshness('unknown') })
    return () => { cancelled = true }
  }, [chunk])

  const renderedText = useMemo(() => renderChunkText(text), [text])
  const parsing = ocring || chunk?.ocr_status === 'running'
  const llmCount = Object.keys(chunk?.metadata_llm || {}).length

  if (!chunk) {
    return (
      <div className="rounded-xl border border-dashed border-border-button px-4 py-10 text-center text-sm text-text-secondary">
        在中间 PDF 上拖出方框新建一段，或点右侧列表选中一段，即可在这里改文字和业务信息。
      </div>
    )
  }

  const editableFields = fields.filter(field => (
    field.extract_source !== 'llm'
    && field.editable
    && field.visible
    && storagePathForField(field).startsWith('business_metadata.')
  ))
  const autoChunk = isAutoChunk(chunk)
  const sourceLabel = autoChunk
    ? '自动生成'
    : chunk.text_source === 'digital'
      ? 'PDF 内嵌文字'
      : chunk.text_source === 'manual'
        ? '人工录入'
        : chunk.text_source === 'pending'
          ? '待识别'
          : chunk.text_source

  const runOcr = async () => {
    setOcring(true)
    try {
      const updated = await api.ocrChunkSync(chunk.id)
      onSaved(updated)
      onQueued()
      toast.success('文字识别完成')
    } catch (error) {
      toast.error('文字识别失败: ' + (error as Error).message)
    } finally {
      setOcring(false)
    }
  }

  const save = async () => {
    setSaving(true)
    try {
      const updated = await api.updateChunk(chunk.id, { text, business_metadata: meta })
      setDirtyText(false)
      setDirtyMeta(false)
      onSaved(updated)
      toast.success('已保存')
    } catch (error) {
      toast.error('保存失败: ' + (error as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const adopt = async (key: string) => {
    const value = suggestionValue(chunk.metadata_llm[key])
    if (value === undefined) return
    const field = fields.find(item => item.field_key === key)
    const path = field ? acceptedPathForField(field) : `business_metadata.${key}`
    const next = setByPath({ business_metadata: meta }, path, value).business_metadata as Record<string, unknown>
    const nextLlm = { ...(chunk.metadata_llm || {}) }
    const rawSuggestion = nextLlm[key]
    if (rawSuggestion && typeof rawSuggestion === 'object') {
      nextLlm[key] = { ...(rawSuggestion as Record<string, unknown>), status: 'accepted' }
    } else {
      nextLlm[key] = { value, status: 'accepted' }
    }
    setMeta(next)
    const updated = await api.updateChunk(chunk.id, { business_metadata: next, metadata_llm: nextLlm })
    setDirtyMeta(false)
    onSaved(updated)
    toast.success('已采纳建议')
  }

  const changeStatus = async (status: ChunkStatus) => {
    setStatusBusy(true)
    try {
      const updated = await api.updateChunk(chunk.id, { status })
      onSaved(updated)
      toast.success(`状态已更新为 ${STATUS_LABEL[status] || status}`)
    } catch (error) {
      toast.error('更新审核状态失败: ' + (error as Error).message)
    } finally {
      setStatusBusy(false)
    }
  }

  const setMetaField = (key: string, value: unknown) => {
    setMeta(prev => setByPath(prev, key, value))
    setDirtyMeta(true)
  }

  const renderField = (field: FieldConfig) => {
    const storagePath = storagePathForField(field)
    const localPath = storagePath.startsWith('business_metadata.')
      ? storagePath.slice('business_metadata.'.length)
      : field.field_key
    const value = getByPath(meta, localPath)

    if (field.value_constraint === 'enum') {
      if (field.value_type === 'list') {
        const arr = Array.isArray(value) ? (value as string[]) : []
        return (
          <div className="space-y-2" key={field.field_key}>
            <Label>{field.display_name}</Label>
            <div className="flex flex-wrap gap-1.5">
              {field.label_list.map(option => (
                <button
                  key={option}
                  type="button"
                  className={cn(
                    'rounded-md border px-2.5 py-1 text-xs transition',
                    arr.includes(option)
                      ? 'border-accent-primary bg-bg-accent text-accent-primary'
                      : 'border-border-button text-text-secondary hover:border-accent-primary/40',
                  )}
                  onClick={() => setMetaField(
                    localPath,
                    arr.includes(option) ? arr.filter(item => item !== option) : [...arr, option],
                  )}
                >
                  {option}
                </button>
              ))}
            </div>
          </div>
        )
      }

      return (
        <div className="space-y-2" key={field.field_key}>
          <Label>{field.display_name}</Label>
          <select
            className="flex h-10 w-full rounded-md border border-border-button bg-bg-input px-3 text-sm"
            value={(value as string) || ''}
            onChange={event => setMetaField(localPath, event.target.value)}
          >
            <option value="">未选择</option>
            {field.label_list.map(option => <option key={option} value={option}>{option}</option>)}
          </select>
        </div>
      )
    }

    if (field.value_type === 'list') {
      const arr = Array.isArray(value) ? (value as string[]) : []
      return (
        <div className="space-y-2" key={field.field_key}>
          <Label>{field.display_name}</Label>
          <Input
            value={arr.join(', ')}
            placeholder="用逗号分隔多个值"
            onChange={event => setMetaField(
              localPath,
              event.target.value.split(/[,，]+/).map(item => item.trim()).filter(Boolean),
            )}
          />
        </div>
      )
    }

    return (
      <div className="space-y-2" key={field.field_key}>
        <Label>{field.display_name}</Label>
        <Input
          value={(value as string) || ''}
          onChange={event => setMetaField(localPath, event.target.value)}
        />
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm text-text-secondary">
          第 {chunk.page} 页 · {sourceLabel}
        </div>
        <Badge variant={STATUS_VARIANT[chunk.status] || 'secondary'}>{STATUS_LABEL[chunk.status] || chunk.status}</Badge>
      </div>

      {chunk.crop_url && (
        <img className="max-h-36 w-full rounded-lg border border-border-button object-contain bg-bg-canvas" src={chunk.crop_url} alt="裁剪预览" />
      )}

      <Tabs value={tab} onValueChange={value => setTab(value as EditorTab)} className="flex min-h-0 flex-1 flex-col">
        <TabsList className="w-full justify-start">
          <TabsTrigger value="content" title={helpText.chunkStudio.editorContent}>内容</TabsTrigger>
          <TabsTrigger value="metadata" title={helpText.chunkStudio.editorMeta}>业务信息</TabsTrigger>
          <TabsTrigger value="review" title={helpText.chunkStudio.editorReview}>审核</TabsTrigger>
          <TabsTrigger value="llm" title={helpText.chunkStudio.editorLlm}>
            AI 建议
            {llmCount > 0 && <span className="ml-1 text-accent-primary">{llmCount}</span>}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="content" className="mt-3 min-h-0 flex-1 space-y-3 overflow-auto">
          <div className="flex items-center justify-between gap-2">
            <Explain text={helpText.chunkStudio.editorContent} title="内容文字">
              <Label>内容文字</Label>
            </Explain>
            <div className="inline-flex rounded-md bg-bg-card p-0.5">
              <Explain text={helpText.chunkStudio.editorSource} title="源码">
                <button
                  type="button"
                  className={cn('rounded px-2.5 py-1 text-xs', textMode === 'source' ? 'bg-bg-base shadow-sm' : 'text-text-secondary')}
                  onClick={() => setTextMode('source')}
                >
                  源码
                </button>
              </Explain>
              <Explain text={helpText.chunkStudio.editorPreview} title="预览">
                <button
                  type="button"
                  className={cn('rounded px-2.5 py-1 text-xs', textMode === 'preview' ? 'bg-bg-base shadow-sm' : 'text-text-secondary')}
                  onClick={() => setTextMode('preview')}
                  disabled={parsing}
                >
                  {parsing ? '解析中…' : '预览'}
                </button>
              </Explain>
            </div>
          </div>

          {textMode === 'source' ? (
            <Textarea
              className="min-h-[180px] font-mono text-xs"
              value={text}
              onChange={event => {
                setText(event.target.value)
                setDirtyText(true)
              }}
            />
          ) : parsing ? (
            <div className="flex min-h-[180px] flex-col items-center justify-center gap-2 rounded-lg border border-border-button bg-bg-canvas text-sm text-text-secondary">
              正在识别文字，请稍候…
            </div>
          ) : (
            <div
              className="legacy-surface rendered-text min-h-[180px] rounded-lg border border-border-button bg-bg-base p-3"
              dangerouslySetInnerHTML={{ __html: renderedText || '<p class="muted">无文本</p>' }}
            />
          )}

          <div className="flex flex-wrap items-center gap-2">
            {!autoChunk && chunk.ocr_status && !parsing && (
              <Badge variant="secondary">识别 {chunk.ocr_status}</Badge>
            )}
            {!autoChunk && chunk.ocr_status === 'failed' && chunk.ocr_error && (
              <span className="text-xs text-state-error">{chunk.ocr_error}</span>
            )}
            {autoChunk ? (
              <span className="text-xs text-text-secondary">自动生成的段落已带文字，一般不用再识别。</span>
            ) : (
              <Explain text={helpText.chunkStudio.ocr} title="识别文字">
                <Button variant="outline" size="sm" onClick={runOcr} disabled={ocring || parsing}>
                  {ocring ? '识别中…' : '识别文字'}
                </Button>
              </Explain>
            )}
            {dirtyText && <span className="text-xs text-state-warning">文字有未保存修改</span>}
          </div>
        </TabsContent>

        <TabsContent value="metadata" className="mt-3 space-y-3 overflow-auto">
          <p className="text-xs text-text-secondary">
            填写标准号、条款号等业务信息，方便以后查找和审查。AI 建议请到「AI 建议」页签采纳。
          </p>
          <div className="space-y-4">
            {editableFields.map(renderField)}
            {!editableFields.length && (
              <p className="text-sm text-text-secondary">当前没有可编辑的业务字段。可到「设置 → 字段配置」添加。</p>
            )}
          </div>
          {dirtyMeta && <span className="text-xs text-state-warning">业务信息有未保存修改</span>}
        </TabsContent>

        <TabsContent value="review" className="mt-3 space-y-4 overflow-auto">
          <div>
            <Explain text={helpText.chunkStudio.editorReview} title="审核状态">
              <h4 className="text-sm font-semibold">审核状态</h4>
            </Explain>
            <p className="mt-1 text-xs text-text-secondary">待处理 → 已复核 → 批准 / 驳回</p>
          </div>
          <div className="flex flex-wrap gap-2">
            {chunk.status === 'pending' && (
              <Button
                size="sm"
                onClick={() => changeStatus('reviewed')}
                disabled={statusBusy || dirtyText || dirtyMeta}
              >
                标记已复核
              </Button>
            )}
            {chunk.status === 'reviewed' && (
              <>
                <Button size="sm" onClick={() => changeStatus('approved')} disabled={statusBusy}>
                  批准
                </Button>
                <Button size="sm" variant="destructive" onClick={() => changeStatus('rejected')} disabled={statusBusy}>
                  驳回
                </Button>
              </>
            )}
            {(chunk.status === 'approved' || chunk.status === 'rejected') && (
              <span className="text-xs text-text-secondary">若再改文字或业务信息，会自动回到「待处理」。</span>
            )}
            {(dirtyText || dirtyMeta) && (
              <span className="text-xs text-state-warning">请先点「保存」，再提交审核。</span>
            )}
          </div>
        </TabsContent>

        <TabsContent value="llm" className="mt-3 space-y-3 overflow-auto">
          {llmCount === 0 ? (
            <p className="py-8 text-center text-sm text-text-secondary">暂无 AI 建议。有建议时会出现在这里，点「采纳」才会写入正式信息。</p>
          ) : (
            <>
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs text-text-secondary">这些只是建议，不会自动生效；点「采纳」后才会写入业务信息。</p>
                <Badge variant="secondary">{suggestionFreshnessLabel(suggestionFreshness)}</Badge>
              </div>
              {Object.entries(chunk.metadata_llm).map(([key, value]) => {
                const suggestion = parseLlmSuggestion(value)
                const items = suggestionItems(suggestion.value)
                return (
                  <section key={key} className="rounded-xl border border-border-button p-3">
                    <div className="mb-2 flex flex-wrap items-center gap-2">
                      <h5 className="text-sm font-semibold">{SUGGESTION_LABELS[key] || key}</h5>
                      <Badge variant={suggestion.status === 'accepted' ? 'success' : 'secondary'}>
                        {suggestionStatusLabel(suggestion.status)}
                      </Badge>
                      <Button
                        size="sm"
                        variant="outline"
                        className="ml-auto"
                        onClick={() => adopt(key)}
                        disabled={suggestion.status === 'accepted' || items.length === 0}
                      >
                        {suggestion.status === 'accepted' ? '已采纳' : '采纳'}
                      </Button>
                    </div>
                    {key === 'questions' ? (
                      <ol className="list-decimal space-y-1 pl-5 text-sm">
                        {items.map(item => <li key={item}>{item}</li>)}
                      </ol>
                    ) : (
                      <div className="flex flex-wrap gap-1.5">
                        {items.map(item => (
                          <Badge key={item} variant="secondary">{item}</Badge>
                        ))}
                      </div>
                    )}
                    <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-text-secondary">
                      <div>版本 · {suggestion.promptVersion || '旧版未标记'}</div>
                      <div>模型 · {suggestion.model || '未记录'}</div>
                      <div>生成 · {formatGeneratedAt(suggestion.generatedAt)}</div>
                      <div title={suggestion.sourceTextSha256}>哈希 · {shortHash(suggestion.sourceTextSha256)}</div>
                    </dl>
                  </section>
                )
              })}
            </>
          )}
        </TabsContent>
      </Tabs>

      <div className="flex gap-2 border-t border-border-button pt-3">
        <Explain text={helpText.chunkStudio.save} title="保存">
          <Button onClick={save} disabled={saving || (!dirtyText && !dirtyMeta)}>
            {saving ? '保存中…' : '保存'}
          </Button>
        </Explain>
        <Explain text={helpText.chunkStudio.deleteChunk} title="删除">
          <Button variant="destructive" onClick={() => onDelete(chunk.id)}>
            删除
          </Button>
        </Explain>
      </div>
    </div>
  )
}

function suggestionStatusLabel(status: string): string {
  if (status === 'accepted') return '已采纳'
  if (status === 'rejected') return '已拒绝'
  return '建议'
}

function suggestionFreshnessLabel(value: SuggestionFreshness): string {
  if (value === 'checking') return '校验正文中'
  if (value === 'current') return '正文一致'
  if (value === 'changed') return '正文已变化'
  return '未校验正文'
}
