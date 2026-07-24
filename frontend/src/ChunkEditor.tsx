import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Pencil, Plus, X } from 'lucide-react'
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
  parseLlmSuggestion,
  suggestionItems,
  textSha256,
} from '@/llmMetadata'
import { Explain } from '@/components/explain'
import { Badge, Input, Textarea } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { helpText } from '@/lib/help-text'
import { renderChunkText } from '@/lib/render-chunk-text'
import { cn } from '@/lib/utils'

interface Props {
  chunk: Chunk | null
  fields: FieldConfig[]
  onSaved: (c: Chunk) => void
  onDelete?: (id: string) => void
  onQueued: () => void
  onCancel?: () => void
}

type TextMode = 'source' | 'preview'
type SuggestionFreshness = 'checking' | 'current' | 'changed' | 'unknown'

const SUGGESTION_LABELS: Record<string, string> = {
  keywords: '关键词',
  questions: '可回答问题',
}

const STATUS_LABEL: Record<string, string> = {
  pending: '待处理',
  reviewed: '已复核',
  approved: '已批准',
  rejected: '已驳回',
}

const STATUS_OPTIONS: ChunkStatus[] = ['pending', 'reviewed', 'approved', 'rejected']

export function ChunkEditor({ chunk, fields, onSaved, onQueued, onCancel }: Props) {
  const [text, setText] = useState('')
  const [meta, setMeta] = useState<Record<string, unknown>>({})
  const [saving, setSaving] = useState(false)
  const [statusBusy, setStatusBusy] = useState(false)
  const [ocring, setOcring] = useState(false)
  const [regenBusy, setRegenBusy] = useState(false)
  const [dirtyText, setDirtyText] = useState(false)
  const [dirtyMeta, setDirtyMeta] = useState(false)
  const [textMode, setTextMode] = useState<TextMode>('preview')
  const [suggestionFreshness, setSuggestionFreshness] = useState<SuggestionFreshness>('unknown')

  useEffect(() => {
    if (!chunk) return
    setText(chunk.text || '')
    setMeta({ ...getBusinessMetadata(chunk) })
    setDirtyText(false)
    setDirtyMeta(false)
    setTextMode('preview')
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
  const llmEntries = Object.entries(chunk?.metadata_llm || {})

  if (!chunk) {
    return (
      <div className="rounded-xl border border-dashed border-border-button px-4 py-10 text-center text-sm text-text-secondary">
        在中间 PDF 上拖出方框新建一段，或点右侧列表选中一段，即可在这里改文字和元数据。
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

  const regenerateSuggestions = async () => {
    if (!chunk.text?.trim()) {
      toast.error('正文为空，无法重新生成')
      return
    }
    setRegenBusy(true)
    try {
      const summary = await api.extractLlmSuggestionsForChunk(chunk.id, { force: true })
      if (!summary.extracted) {
        toast.error('未能生成建议，请稍后重试')
        return
      }
      const updated = await api.getChunk(chunk.id)
      onSaved(updated)
      toast.success('已按模板重新生成关键词和问题')
    } catch (error) {
      toast.error('重新生成失败: ' + (error as Error).message)
    } finally {
      setRegenBusy(false)
    }
  }

  const updateSuggestionItems = async (key: string, items: string[]) => {
    const nextLlm = { ...(chunk.metadata_llm || {}) }
    const rawSuggestion = nextLlm[key]
    const suggestion = parseLlmSuggestion(rawSuggestion)
    if (rawSuggestion && typeof rawSuggestion === 'object' && !Array.isArray(rawSuggestion)) {
      nextLlm[key] = { ...(rawSuggestion as Record<string, unknown>), value: items }
    } else {
      nextLlm[key] = { value: items, status: suggestion.status || 'suggested' }
    }

    const payload: { metadata_llm: Record<string, unknown>; business_metadata?: Record<string, unknown> } = {
      metadata_llm: nextLlm,
    }
    if (suggestion.status === 'accepted') {
      const field = fields.find(item => item.field_key === key)
      const path = field ? acceptedPathForField(field) : `business_metadata.${key}`
      const next = setByPath({ business_metadata: meta }, path, items).business_metadata as Record<string, unknown>
      setMeta(next)
      payload.business_metadata = next
      setDirtyMeta(false)
    }

    try {
      const updated = await api.updateChunk(chunk.id, payload)
      onSaved(updated)
    } catch (error) {
      toast.error('更新建议失败: ' + (error as Error).message)
    }
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
    const wrap = (control: ReactNode) => (
      <MetaFieldRow key={field.field_key} label={field.display_name}>
        {control}
      </MetaFieldRow>
    )

    // 自动抽取字段：只读展示，不用置灰输入框
    if (field.extract_source === 'auto') {
      return wrap(<ReadonlyFieldValue field={field} value={value} />)
    }

    if (field.value_constraint === 'enum') {
      if (field.value_type === 'list') {
        // Empty enum options (e.g. legacy 用户标签) — fall back to free-form tags.
        if (!field.label_list.length) {
          const arr = Array.isArray(value) ? (value as string[]) : []
          return wrap(
            <FreeTagInput
              values={arr}
              onChange={next => setMetaField(localPath, next)}
            />,
          )
        }
        const arr = Array.isArray(value) ? (value as string[]) : []
        return wrap(
          <div className="flex flex-wrap gap-1">
            {field.label_list.map(option => (
              <button
                key={option}
                type="button"
                className={cn(
                  'rounded-full border px-2 py-0.5 text-xs transition',
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
          </div>,
        )
      }

      return wrap(
        <select
          className="h-7 max-w-full rounded-md border border-border-button bg-bg-input px-2 text-xs"
          value={(value as string) || ''}
          onChange={event => setMetaField(localPath, event.target.value)}
        >
          <option value="">未选择</option>
          {field.label_list.map(option => <option key={option} value={option}>{option}</option>)}
        </select>,
      )
    }

    if (field.value_type === 'list') {
      const arr = Array.isArray(value) ? (value as string[]) : []
      return wrap(
        <FreeTagInput
          values={arr}
          onChange={next => setMetaField(localPath, next)}
        />,
      )
    }

    return wrap(
      <Input
        className="h-7 text-xs"
        value={(value as string) || ''}
        onChange={event => setMetaField(localPath, event.target.value)}
      />,
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-2">
        <div className="min-w-0 text-sm text-text-secondary">
          第 {chunk.page} 页 · {sourceLabel}
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Explain text={helpText.chunkStudio.editorReview} title="审核状态">
            <select
              className={cn(
                'h-8 rounded-md border border-border-button bg-bg-input px-2 text-xs font-medium outline-none focus:border-accent-primary focus:ring-2 focus:ring-accent-primary/20',
                chunk.status === 'approved' && 'text-state-success',
                chunk.status === 'pending' && 'text-state-warning',
                chunk.status === 'rejected' && 'text-state-error',
              )}
              value={chunk.status}
              disabled={statusBusy || dirtyText || dirtyMeta}
              title={dirtyText || dirtyMeta ? '请先保存后再改状态' : '更换审核状态'}
              onChange={event => void changeStatus(event.target.value as ChunkStatus)}
            >
              {STATUS_OPTIONS.map(option => (
                <option key={option} value={option}>{STATUS_LABEL[option]}</option>
              ))}
            </select>
          </Explain>
          <Explain text={helpText.chunkStudio.save} title="保存">
            <Button size="sm" onClick={save} disabled={saving || (!dirtyText && !dirtyMeta)}>
              {saving ? '保存中…' : '保存'}
            </Button>
          </Explain>
          {onCancel && (
            <Button size="sm" variant="outline" onClick={onCancel}>
              取消
            </Button>
          )}
        </div>
      </div>

      <div className="min-h-0 flex-1 space-y-8 overflow-auto pr-1">
        <section className="space-y-3">
          <div className="flex items-center justify-between gap-2">
            <SectionHeading>内容</SectionHeading>
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
            {!autoChunk && (
              <Explain text={helpText.chunkStudio.ocr} title="识别文字">
                <Button variant="outline" size="sm" onClick={runOcr} disabled={ocring || parsing}>
                  {ocring ? '识别中…' : '识别文字'}
                </Button>
              </Explain>
            )}
            {dirtyText && <span className="text-xs text-state-warning">文字有未保存修改</span>}
          </div>
        </section>

        <section className="space-y-4 border-t border-border-button pt-6">
          <SectionHeading>
            元数据
          </SectionHeading>
          <div className="space-y-1.5 rounded-xl border border-border-button p-3">
            {editableFields.map(renderField)}
            {!editableFields.length && (
              <p className="text-sm text-text-secondary">当前没有可编辑的业务字段。可到「设置 → 字段配置」添加。</p>
            )}
          </div>
          {dirtyMeta && <span className="text-xs text-state-warning">元数据有未保存修改</span>}
        </section>

        <section className="space-y-3 border-t border-border-button pt-6">
          <div className="flex items-start justify-between gap-2">
            <Explain text={helpText.chunkStudio.editorLlm} title="AI 建议">
              <SectionHeading>AI 建议</SectionHeading>
            </Explain>
            {llmEntries.length > 0 && (
              <Badge variant="secondary">{suggestionFreshnessLabel(suggestionFreshness)}</Badge>
            )}
          </div>
          {llmEntries.length === 0 ? (
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-sm text-text-secondary">暂无 AI 建议</p>
              <Button size="sm" variant="outline" disabled={regenBusy} onClick={() => void regenerateSuggestions()}>
                {regenBusy ? '生成中…' : '重新生成'}
              </Button>
            </div>
          ) : (
            <>
              {llmEntries.map(([key, value]) => {
                const suggestion = parseLlmSuggestion(value)
                const items = suggestionItems(suggestion.value)
                return (
                  <section key={key} className="rounded-xl border border-border-button p-3">
                    <div className="mb-2 flex flex-wrap items-center gap-2">
                      <h5 className="text-sm font-semibold">{SUGGESTION_LABELS[key] || key}</h5>
                      <div className="ml-auto flex items-center gap-2">
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={regenBusy}
                          onClick={() => void regenerateSuggestions()}
                        >
                          {regenBusy ? '生成中…' : '重新生成'}
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => adopt(key)}
                          disabled={suggestion.status === 'accepted' || items.length === 0 || regenBusy}
                        >
                          {suggestion.status === 'accepted' ? '已采纳' : '采纳'}
                        </Button>
                      </div>
                    </div>
                    <FreeTagInput
                      values={items}
                      onChange={next => void updateSuggestionItems(key, next)}
                      editable={key === 'questions'}
                      stack={key === 'questions'}
                      addPlaceholder={key === 'questions' ? '输入问题' : '输入标签'}
                    />
                  </section>
                )
              })}
            </>
          )}
        </section>
      </div>
    </div>
  )
}

function SectionHeading({
  children,
  description,
}: {
  children: ReactNode
  description?: string
}) {
  return (
    <div className="space-y-1">
      <h4 className="border-l-2 border-accent-primary pl-2.5 text-base font-semibold tracking-wide text-text-primary">
        {children}
      </h4>
      {description && (
        <p className="pl-3 text-xs leading-relaxed text-text-secondary">{description}</p>
      )}
    </div>
  )
}

function suggestionFreshnessLabel(value: SuggestionFreshness): string {
  if (value === 'checking') return '校验正文中'
  if (value === 'current') return '正文一致'
  if (value === 'changed') return '正文已变化'
  return '未校验正文'
}

const AUTO_VALUE_LABELS: Record<string, Record<string, string>> = {
  content_type: {
    table: '表格',
    text: '文本',
    section: '标题',
    image: '图片',
  },
  table_kind: {
    numbered_table: '编号表',
    continued_table: '续表',
    symbol_table: '符号表',
    formula_table: '公式表',
    report_form: '报告表单',
    calculation_table: '计算表',
    unnumbered_table: '无编号表',
  },
}

function MetaFieldRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-start gap-3 py-1">
      <span className="w-16 shrink-0 pt-0.5 text-right text-[11px] leading-5 text-text-secondary">
        {label}
      </span>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  )
}

function ReadonlyFieldValue({ field, value }: { field: FieldConfig; value: unknown }) {
  const pillClass =
    'inline-flex max-w-full whitespace-normal break-words rounded-full border border-border-button/70 px-2 py-0.5 text-xs text-text-primary'

  if (field.value_type === 'list') {
    const arr = Array.isArray(value) ? value.map(String).map(item => item.trim()).filter(Boolean) : []
    if (!arr.length) {
      return <span className="text-xs leading-5 text-text-secondary">未抽取到</span>
    }
    return (
      <div className="flex flex-wrap gap-1">
        {arr.map(tag => (
          <span key={tag} className={pillClass}>{tag}</span>
        ))}
      </div>
    )
  }

  const raw = value == null ? '' : String(value).trim()
  if (!raw) {
    return <span className="text-xs leading-5 text-text-secondary">未抽取到</span>
  }
  const label = AUTO_VALUE_LABELS[field.field_key]?.[raw] || raw
  return (
    <div className="flex flex-wrap gap-1">
      <span className={pillClass}>{label}</span>
    </div>
  )
}

function FreeTagInput({
  values,
  onChange,
  editable = false,
  stack = false,
  addPlaceholder = '输入标签',
}: {
  values: string[]
  onChange: (next: string[]) => void
  editable?: boolean
  stack?: boolean
  addPlaceholder?: string
}) {
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState('')
  const [editingIndex, setEditingIndex] = useState<number | null>(null)
  const [editDraft, setEditDraft] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const editRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (adding) inputRef.current?.focus()
  }, [adding])

  useEffect(() => {
    if (editingIndex !== null) editRef.current?.focus()
  }, [editingIndex])

  const commitAdd = () => {
    const next = draft.trim()
    setDraft('')
    setAdding(false)
    if (!next || values.includes(next)) return
    onChange([...values, next])
  }

  const commitEdit = () => {
    if (editingIndex === null) return
    const index = editingIndex
    const nextValue = editDraft.trim()
    setEditingIndex(null)
    setEditDraft('')
    if (!nextValue) {
      onChange(values.filter((_, i) => i !== index))
      return
    }
    if (nextValue === values[index]) return
    onChange(values.map((item, i) => (i === index ? nextValue : item)))
  }

  const startEdit = (index: number) => {
    setAdding(false)
    setEditingIndex(index)
    setEditDraft(values[index] || '')
  }

  return (
    <div className={cn('flex gap-1.5', stack ? 'flex-col items-stretch' : 'flex-wrap items-center')}>
      {values.map((tag, index) => (
        editingIndex === index ? (
          <input
            key={`edit-${index}`}
            ref={editRef}
            className={cn(
              'rounded-full border border-accent-primary/40 bg-bg-input px-2.5 text-xs outline-none focus:ring-2 focus:ring-accent-primary/20',
              stack ? 'h-8 w-full' : 'h-7 min-w-[7rem]',
            )}
            value={editDraft}
            onChange={event => setEditDraft(event.target.value)}
            onBlur={commitEdit}
            onKeyDown={event => {
              if (event.key === 'Enter') {
                event.preventDefault()
                commitEdit()
              }
              if (event.key === 'Escape') {
                setEditingIndex(null)
                setEditDraft('')
              }
            }}
          />
        ) : (
          <span
            key={`${index}-${tag}`}
          className={cn(
            'group inline-flex items-center gap-1 rounded-full border border-border-button/80 bg-bg-base/50 px-2 py-0.5 text-xs text-text-primary',
            stack ? 'w-full max-w-full' : 'max-w-full',
          )}
          >
            <span className={cn('min-w-0 flex-1', stack ? 'whitespace-normal break-words' : 'truncate')}>
              {tag}
            </span>
            {editable && (
              <button
                type="button"
                className="hidden size-3.5 shrink-0 items-center justify-center rounded-full text-text-secondary hover:bg-bg-card hover:text-accent-primary group-hover:inline-flex"
                aria-label={`修改 ${tag}`}
                onClick={() => startEdit(index)}
              >
                <Pencil className="size-3" />
              </button>
            )}
            <button
              type="button"
              className="hidden size-3.5 shrink-0 items-center justify-center rounded-full text-text-secondary hover:bg-bg-card hover:text-state-error group-hover:inline-flex"
              aria-label={`删除 ${tag}`}
              onClick={() => onChange(values.filter((_, i) => i !== index))}
            >
              <X className="size-3" />
            </button>
          </span>
        )
      ))}
      {adding ? (
        <input
          ref={inputRef}
          className={cn(
            'rounded-full border border-accent-primary/40 bg-bg-input px-2.5 text-xs outline-none focus:ring-2 focus:ring-accent-primary/20',
            stack ? 'h-8 w-full' : 'h-7 min-w-[7rem]',
          )}
          value={draft}
          placeholder={addPlaceholder}
          onChange={event => setDraft(event.target.value)}
          onBlur={commitAdd}
          onKeyDown={event => {
            if (event.key === 'Enter') {
              event.preventDefault()
              commitAdd()
            }
            if (event.key === 'Escape') {
              setDraft('')
              setAdding(false)
            }
          }}
        />
      ) : (
        <button
          type="button"
          className={cn(
            'inline-flex items-center justify-center rounded-full border border-dashed border-border-button text-text-secondary transition hover:border-accent-primary/50 hover:text-accent-primary',
            stack ? 'h-6 w-6' : 'size-6',
          )}
          aria-label="添加"
          onClick={() => {
            setEditingIndex(null)
            setAdding(true)
          }}
        >
          <Plus className="size-3" />
        </button>
      )}
    </div>
  )
}
