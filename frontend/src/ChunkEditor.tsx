import { useEffect, useMemo, useState } from 'react'
import { api, type Chunk, type FieldConfig } from './api'

interface Props {
  chunk: Chunk | null
  fields: FieldConfig[]
  onSaved: (c: Chunk) => void
  onDelete: (id: string) => void
  onQueued: () => void
}

type TextMode = 'source' | 'preview'

export function ChunkEditor({ chunk, fields, onSaved, onDelete, onQueued }: Props) {
  const [text, setText] = useState('')
  const [meta, setMeta] = useState<Record<string, unknown>>({})
  const [saving, setSaving] = useState(false)
  const [ocring, setOcring] = useState(false)
  const [dirtyText, setDirtyText] = useState(false)
  const [textMode, setTextMode] = useState<TextMode>('preview')
  const [showMeta, setShowMeta] = useState(false)

  useEffect(() => {
    if (!chunk) return
    setText(chunk.text || '')
    setMeta({ ...(chunk.metadata || {}) })
    setDirtyText(false)
    setTextMode('preview')
  }, [chunk?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!chunk || dirtyText) return
    setText(chunk.text || '')
  }, [chunk?.text, chunk?.updated_at, dirtyText]) // eslint-disable-line react-hooks/exhaustive-deps

  const renderedText = useMemo(() => renderChunkText(text), [text])

  if (!chunk) {
    return (
      <div className="editor empty">
        选中一个切片，或在页面上框选新建。OCR 和元数据编辑会显示在这里。
      </div>
    )
  }

  const editableFields = fields.filter(field => field.extract_source !== 'llm')
  const ocrBusy = chunk.ocr_status === 'queued' || chunk.ocr_status === 'running'

  const runOcr = async () => {
    setOcring(true)
    try {
      const updated = await api.ocrChunk(chunk.id)
      onSaved(updated)
      onQueued()
    } catch (error) {
      alert('OCR 入队失败: ' + (error as Error).message)
    } finally {
      setOcring(false)
    }
  }

  const save = async () => {
    setSaving(true)
    try {
      const updated = await api.updateChunk(chunk.id, { text, metadata: meta })
      setDirtyText(false)
      onSaved(updated)
    } catch (error) {
      alert('保存失败: ' + (error as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const adopt = async (key: string) => {
    const value = chunk.metadata_llm[key]
    if (value === undefined) return
    const next = { ...meta, [key]: value }
    setMeta(next)
    const updated = await api.updateChunk(chunk.id, { metadata: next })
    onSaved(updated)
  }

  const setMetaField = (key: string, value: unknown) => {
    setMeta(prev => ({ ...prev, [key]: value }))
  }

  const renderField = (field: FieldConfig) => {
    const value = meta[field.field_key]

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
                    field.field_key,
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
            onChange={event => setMetaField(field.field_key, event.target.value)}
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
              field.field_key,
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
          onChange={event => setMetaField(field.field_key, event.target.value)}
        />
      </div>
    )
  }

  return (
    <div className="editor">
      <div className="editor-head">
        <span>第 {chunk.page} 页 · {chunk.text_source}</span>
        <span className={`status ${chunk.status}`}>{chunk.status}</span>
      </div>

      {chunk.crop_url && <img className="crop-preview" src={chunk.crop_url} alt="crop" />}

      <div className="editor-section-title">
        <label>切片文本</label>
        <div className="segmented">
          <button className={textMode === 'source' ? 'on' : ''} onClick={() => setTextMode('source')}>源码</button>
          <button className={textMode === 'preview' ? 'on' : ''} onClick={() => setTextMode('preview')}>渲染</button>
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
      ) : (
        <div
          className="rendered-text"
          dangerouslySetInnerHTML={{ __html: renderedText || '<p class="muted">无文本</p>' }}
        />
      )}

      <div className="ocr-actions">
        {chunk.ocr_status && <span className={`job-status ${chunk.ocr_status}`}>OCR {chunk.ocr_status}</span>}
        {chunk.ocr_status === 'failed' && chunk.ocr_error && <span className="muted">{chunk.ocr_error}</span>}
        <button onClick={runOcr} disabled={ocring || ocrBusy}>
          {ocring ? '加入中...' : '加入 MinerU OCR 队列'}
        </button>
        {dirtyText && <span className="muted">文本有未保存修改</span>}
      </div>

      <div className="metadata-box">
        <button className="metadata-toggle" onClick={() => setShowMeta(v => !v)}>
          {showMeta ? '收起元数据' : '展开元数据'}
        </button>
        <p className="muted">
          chunk 级元数据。auto 字段在创建/OCR 完成时由正则自动填入（不覆盖手改）；llm 字段在下方采纳。
        </p>
        {showMeta && <div className="fields">{editableFields.map(renderField)}</div>}
      </div>

      {Object.keys(chunk.metadata_llm || {}).length > 0 && (
        <div className="llm-suggestions">
          <h4>LLM 建议</h4>
          {Object.entries(chunk.metadata_llm).map(([key, value]) => (
            <div key={key} className="llm-row">
              <span className="k">{key}</span>
              <span className="v">{JSON.stringify(value)}</span>
              <button onClick={() => adopt(key)}>采纳</button>
            </div>
          ))}
        </div>
      )}

      <div className="editor-actions">
        <button onClick={save} disabled={saving}>{saving ? '保存中...' : '保存'}</button>
        <button
          onClick={() => { if (confirm('删除这个切片?')) onDelete(chunk.id) }}
          className="danger"
        >
          删除
        </button>
      </div>
    </div>
  )
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
