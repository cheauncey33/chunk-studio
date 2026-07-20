import { useEffect, useMemo, useState } from 'react'
import { api, type Chunk, type ChunkStatus, type FieldConfig } from './api'
import {
  acceptedPathForField,
  getByPath,
  getBusinessMetadata,
  isAutoChunk,
  setByPath,
  storagePathForField,
  suggestionValue,
} from './chunkSchema'
import {
  formatGeneratedAt,
  parseLlmSuggestion,
  shortHash,
  suggestionItems,
  textSha256,
} from './llmMetadata'

interface Props {
  chunk: Chunk | null
  fields: FieldConfig[]
  onSaved: (c: Chunk) => void
  onDelete: (id: string) => void
  onQueued: () => void
}

type TextMode = 'source' | 'preview'
type SuggestionFreshness = 'checking' | 'current' | 'changed' | 'unknown'

const SUGGESTION_LABELS: Record<string, string> = {
  keywords: '关键词',
  questions: '可回答问题',
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
  const [showMeta, setShowMeta] = useState(false)
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

  if (!chunk) {
    return (
      <div className="editor empty">
        选中一个切片，或在页面上框选新建。OCR 和元数据编辑会显示在这里。
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
  const sourceLabel = autoChunk ? 'auto' : chunk.text_source

  const runOcr = async () => {
    setOcring(true)
    try {
      const updated = await api.ocrChunkSync(chunk.id)
      onSaved(updated)
      onQueued()
    } catch (error) {
      alert('MinerU 解析失败: ' + (error as Error).message)
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
    } catch (error) {
      alert('保存失败: ' + (error as Error).message)
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
  }

  const changeStatus = async (status: ChunkStatus) => {
    setStatusBusy(true)
    try {
      const updated = await api.updateChunk(chunk.id, { status })
      onSaved(updated)
    } catch (error) {
      alert('更新审核状态失败: ' + (error as Error).message)
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
          <div className="field" key={field.field_key}>
            <label>{field.display_name}</label>
            <div className="chips">
              {field.label_list.map(option => (
                <button
                  key={option}
                  type="button"
                  className={`chip ${arr.includes(option) ? 'on' : ''}`}
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
        <div className="field" key={field.field_key}>
          <label>{field.display_name}</label>
          <select
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
        <div className="field" key={field.field_key}>
          <label>{field.display_name}</label>
          <input
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
      <div className="field" key={field.field_key}>
        <label>{field.display_name}</label>
        <input
          value={(value as string) || ''}
          onChange={event => setMetaField(localPath, event.target.value)}
        />
      </div>
    )
  }

  return (
    <div className="editor">
      <div className="editor-head">
        <span>第 {chunk.page} 页 · {sourceLabel}</span>
        <span className={`status ${chunk.status}`}>{chunk.status}</span>
      </div>

      {chunk.crop_url && <img className="crop-preview" src={chunk.crop_url} alt="crop" />}

      <div className="editor-section-title">
        <label>切片文本</label>
        <div className="segmented">
          <button className={textMode === 'source' ? 'on' : ''} onClick={() => setTextMode('source')}>源码</button>
          <button
            className={`${textMode === 'preview' ? 'on' : ''} ${parsing ? 'loading' : ''}`}
            onClick={() => setTextMode('preview')}
            disabled={parsing}
          >
            {parsing ? <><span className="spinner" />解析中</> : '渲染'}
          </button>
        </div>
      </div>

      {textMode === 'source' ? (
        <textarea
          value={text}
          onChange={event => {
            setText(event.target.value)
            setDirtyText(true)
          }}
          rows={8}
        />
      ) : parsing ? (
        <div className="rendered-text parsing">
          <span className="spinner" />
          <p>MinerU 正在解析切片，请稍候…</p>
        </div>
      ) : (
        <div
          className="rendered-text"
          dangerouslySetInnerHTML={{ __html: renderedText || '<p class="muted">无文本</p>' }}
        />
      )}

      <div className="ocr-actions">
        {!autoChunk && chunk.ocr_status && !parsing && (
          <span className={`job-status ${chunk.ocr_status}`}>OCR {chunk.ocr_status}</span>
        )}
        {!autoChunk && chunk.ocr_status === 'failed' && chunk.ocr_error && <span className="muted">{chunk.ocr_error}</span>}
        {autoChunk ? (
          <span className="muted">自动切片已包含 MinerU 解析文本，不需要再解析。</span>
        ) : (
          <button onClick={runOcr} disabled={ocring || parsing}>
            {ocring ? <><span className="spinner" />解析中…</> : 'MinerU 解析'}
          </button>
        )}
        {dirtyText && <span className="muted">文本有未保存修改</span>}
      </div>

      <div className="metadata-box">
        <button className="metadata-toggle" onClick={() => setShowMeta(v => !v)}>
          {showMeta ? '收起元数据' : '展开元数据'}
        </button>
        <p className="muted">
          chunk 级业务元数据。auto 字段在创建/OCR 完成时写入 business_metadata（不覆盖手改）；llm 字段在下方采纳。
        </p>
        {showMeta && <div className="fields">{editableFields.map(renderField)}</div>}
      </div>

      <div className="review-workflow">
        <div>
          <h4>审核状态</h4>
          <p className="muted">pending → reviewed → approved / rejected</p>
        </div>
        <div className="review-actions">
          {chunk.status === 'pending' && (
            <button onClick={() => changeStatus('reviewed')} disabled={statusBusy || dirtyText || dirtyMeta}>
              标记已复核
            </button>
          )}
          {chunk.status === 'reviewed' && (
            <>
              <button className="primary" onClick={() => changeStatus('approved')} disabled={statusBusy}>
                批准
              </button>
              <button className="danger" onClick={() => changeStatus('rejected')} disabled={statusBusy}>
                驳回
              </button>
            </>
          )}
          {(chunk.status === 'approved' || chunk.status === 'rejected') && (
            <span className="muted">修改文本或元数据后将自动回到 pending。</span>
          )}
          {(dirtyText || dirtyMeta) && <span className="muted">请先保存内容修改再提交审核。</span>}
        </div>
      </div>

      {Object.keys(chunk.metadata_llm || {}).length > 0 && (
        <div className="llm-suggestions">
          <div className="llm-suggestions-head">
            <div>
              <h4>LLM 检索元数据</h4>
              <p className="muted">建议层不会自动参与生产检索，采纳后写入业务元数据。</p>
            </div>
            <span className={`suggestion-freshness ${suggestionFreshness}`}>
              {suggestionFreshnessLabel(suggestionFreshness)}
            </span>
          </div>
          {Object.entries(chunk.metadata_llm).map(([key, value]) => {
            const suggestion = parseLlmSuggestion(value)
            const items = suggestionItems(suggestion.value)
            return (
              <section key={key} className="llm-suggestion-block">
                <div className="llm-suggestion-title">
                  <h5>{SUGGESTION_LABELS[key] || key}</h5>
                  <span className={`suggestion-status ${suggestion.status}`}>
                    {suggestionStatusLabel(suggestion.status)}
                  </span>
                  <button
                    type="button"
                    onClick={() => adopt(key)}
                    disabled={suggestion.status === 'accepted' || items.length === 0}
                  >
                    {suggestion.status === 'accepted' ? '已采纳' : '采纳'}
                  </button>
                </div>
                {key === 'questions' ? (
                  <ol className="llm-question-list">
                    {items.map(item => <li key={item}>{item}</li>)}
                  </ol>
                ) : (
                  <div className="llm-keyword-list">
                    {items.map(item => <span key={item}>{item}</span>)}
                  </div>
                )}
                <dl className="llm-provenance">
                  <div><dt>版本</dt><dd>{suggestion.promptVersion || '旧版未标记'}</dd></div>
                  <div><dt>模型</dt><dd>{suggestion.model || '未记录'}</dd></div>
                  <div><dt>生成</dt><dd>{formatGeneratedAt(suggestion.generatedAt)}</dd></div>
                  <div><dt>正文哈希</dt><dd className="mono" title={suggestion.sourceTextSha256}>{shortHash(suggestion.sourceTextSha256)}</dd></div>
                </dl>
              </section>
            )
          })}
        </div>
      )}

      <div className="editor-actions">
        <button onClick={save} disabled={saving}>{saving ? '保存中...' : '保存'}</button>
        <button
          onClick={() => onDelete(chunk.id)}
          className="danger"
        >
          删除
        </button>
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

function renderChunkText(raw: string): string {
  const text = raw.trim()
  if (!text) return ''

  // Preserve embedded HTML <table>…</table> blocks (OCR/MinerU output) and
  // render everything else as markdown/text. Only the table HTML is injected
  // raw (sanitized); surrounding lines go through the normal line renderer so
  // titles, headings and paragraphs still show alongside the table.
  const parts: string[] = []
  const tableRe = /<table[\s\S]*?<\/table>/gi
  let lastIndex = 0
  let m: RegExpExecArray | null
  while ((m = tableRe.exec(text)) !== null) {
    if (m.index > lastIndex) {
      parts.push(renderTextLines(text.slice(lastIndex, m.index)))
    }
    parts.push(sanitizeTableHtml(m[0]))
    lastIndex = m.index + m[0].length
  }
  if (lastIndex < text.length) {
    parts.push(renderTextLines(text.slice(lastIndex)))
  }
  return parts.join('')
}

function renderTextLines(block: string): string {
  const lines = block.split(/\r?\n/)
  const html: string[] = []

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i]
    if (!line.trim()) continue

    if (isMarkdownTableStart(lines, i)) {
      const { table, nextIndex } = renderMarkdownTable(lines, i)
      html.push(table)
      i = nextIndex - 1
      continue
    }

    const heading = line.match(/^(#{1,4})\s+(.+)$/)
    if (heading) {
      const level = heading[1].length
      html.push(`<h${level}>${escapeHtml(heading[2])}</h${level}>`)
      continue
    }

    html.push(`<p>${escapeHtml(line)}</p>`)
  }

  return html.join('')
}

function isMarkdownTableStart(lines: string[], index: number): boolean {
  const current = lines[index]
  const next = lines[index + 1]
  return !!current?.includes('|') && /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(next || '')
}

function renderMarkdownTable(lines: string[], start: number): { table: string; nextIndex: number } {
  const header = splitTableRow(lines[start])
  let index = start + 2
  const rows: string[][] = []

  while (index < lines.length && lines[index].includes('|') && lines[index].trim()) {
    rows.push(splitTableRow(lines[index]))
    index += 1
  }

  const thead = `<thead><tr>${header.map(cell => `<th>${escapeHtml(cell)}</th>`).join('')}</tr></thead>`
  const tbody = `<tbody>${rows.map(row => (
    `<tr>${header.map((_, cellIndex) => `<td>${escapeHtml(row[cellIndex] || '')}</td>`).join('')}</tr>`
  )).join('')}</tbody>`

  return { table: `<table>${thead}${tbody}</table>`, nextIndex: index }
}

function splitTableRow(line: string): string[] {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => cell.trim())
}

function sanitizeTableHtml(html: string): string {
  const tableMatch = html.match(/<table[\s\S]*?<\/table>/i)
  if (!tableMatch) return `<pre>${escapeHtml(html)}</pre>`
  return tableMatch[0]
    .replace(/<script[\s\S]*?<\/script>/gi, '')
    .replace(/\son\w+="[^"]*"/gi, '')
    .replace(/\son\w+='[^']*'/gi, '')
    .replace(/\sstyle="[^"]*"/gi, '')
    .replace(/\sstyle='[^']*'/gi, '')
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}
