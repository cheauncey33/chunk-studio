import { useMemo, useState, type FormEvent } from 'react'
import { api, type VectorSearchHit, type VectorSearchResponse } from './api'

interface Props {
  hidden: boolean
  onLocate: (hit: VectorSearchHit) => void
}

interface ParsedCell {
  text: string
  colSpan: number
  rowSpan: number
  header: boolean
}

type ContentSegment =
  | { type: 'text'; value: string }
  | { type: 'table'; rows: ParsedCell[][] }

const META_FIELDS: Array<{ key: string; label: string }> = [
  { key: 'standard_no', label: '标准号' },
  { key: 'section', label: '条款' },
  { key: 'section_title', label: '条款标题' },
  { key: 'table_no', label: '表号' },
  { key: 'table_title', label: '表标题' },
  { key: 'content_type', label: '内容类型' },
]

export function SearchPage({ hidden, onLocate }: Props) {
  const [query, setQuery] = useState('')
  const [topK, setTopK] = useState(10)
  const [result, setResult] = useState<VectorSearchResponse | null>(null)
  const [elapsedMs, setElapsedMs] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const search = async (event: FormEvent) => {
    event.preventDefault()
    const normalized = query.trim()
    if (!normalized || loading) return
    setLoading(true)
    setError('')
    const startedAt = performance.now()
    try {
      const response = await api.searchChunks(normalized, topK)
      setResult(response)
      setElapsedMs(Math.round(performance.now() - startedAt))
    } catch (err) {
      setResult(null)
      setElapsedMs(null)
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="search-page" hidden={hidden}>
      <div className="search-shell">
        <header className="search-heading">
          <div>
            <h2>检索测试</h2>
            <p>使用已批准切片的向量索引，检查查询与原始证据的匹配效果。</p>
          </div>
        </header>

        <form className="search-form" onSubmit={search}>
          <label className="search-query-field">
            <span>测试问题</span>
            <textarea
              rows={3}
              value={query}
              onChange={event => setQuery(event.target.value)}
              placeholder="例如：400 kVA 配电变压器的负载损耗要求是什么？"
            />
          </label>
          <div className="search-controls">
            <label>
              <span>返回数量</span>
              <input
                type="number"
                min={1}
                max={50}
                value={topK}
                onChange={event => setTopK(clampTopK(Number(event.target.value)))}
              />
            </label>
            <button className="primary search-submit" type="submit" disabled={!query.trim() || loading}>
              {loading ? '检索中…' : '开始检索'}
            </button>
          </div>
        </form>

        {error && (
          <div className="search-error" role="alert">
            <strong>检索失败</strong>
            <span>{friendlyError(error)}</span>
          </div>
        )}

        {result && (
          <>
            <section className="search-summary" aria-label="检索摘要">
              <div><strong>{result.hits.length}</strong><span>返回结果</span></div>
              <div><strong>{result.total_candidates}</strong><span>候选切片</span></div>
              <div><strong>{elapsedMs ?? '—'}<small>{elapsedMs == null ? '' : ' ms'}</small></strong><span>本次耗时</span></div>
              <div className="search-model"><strong>{result.model}</strong><span>{result.dimension} 维向量</span></div>
            </section>

            <section className="search-results" aria-label="检索结果">
              <div className="search-results-head">
                <h3>检索结果</h3>
                <span>查询：{result.query}</span>
              </div>
              {result.hits.length === 0 && <div className="search-empty">当前索引没有返回匹配结果。</div>}
              {result.hits.map((hit, index) => (
                <SearchResult
                  key={hit.chunk_id}
                  hit={hit}
                  rank={index + 1}
                  onLocate={onLocate}
                />
              ))}
            </section>
          </>
        )}

        {!result && !error && (
          <div className="search-placeholder">
            <strong>输入一个真实业务问题开始测试</strong>
            <span>结果会保留文件、页码、切片正文和原始裁剪证据，便于人工判断相关性。</span>
          </div>
        )}
      </div>
    </main>
  )
}

function SearchResult({ hit, rank, onLocate }: {
  hit: VectorSearchHit
  rank: number
  onLocate: (hit: VectorSearchHit) => void
}) {
  const metadata = META_FIELDS.flatMap(field => {
    const value = hit.business_metadata[field.key]
    if (value == null || value === '') return []
    const text = Array.isArray(value) ? value.join('、') : String(value)
    return [{ ...field, value: text }]
  })

  return (
    <article className="search-result">
      <div className="result-rank">#{rank}</div>
      <div className="result-content">
        <header className="result-head">
          <div>
            <h4>{hit.file_name}</h4>
            <span>第 {hit.page} 页 · 切片 {shortId(hit.chunk_id)}</span>
          </div>
          <div className="result-score" title="余弦相似度">
            <strong>{hit.score.toFixed(3)}</strong>
            <span>相似度</span>
          </div>
        </header>

        {metadata.length > 0 && (
          <dl className="result-metadata">
            {metadata.map(item => (
              <div key={item.key}>
                <dt>{item.label}</dt>
                <dd>{item.value}</dd>
              </div>
            ))}
          </dl>
        )}

        <details className="result-evidence" open>
          <summary>Chunk 原文</summary>
          <RenderedChunkContent text={hit.text} />
        </details>

        <footer className="result-actions">
          {hit.crop_url && (
            <details className="crop-evidence">
              <summary>查看裁剪证据</summary>
              <img src={hit.crop_url} alt={`${hit.file_name} 第 ${hit.page} 页切片裁剪`} loading="lazy" />
            </details>
          )}
          <button type="button" onClick={() => onLocate(hit)}>在文档中查看</button>
        </footer>
      </div>
    </article>
  )
}

function RenderedChunkContent({ text }: { text: string }) {
  const segments = useMemo(() => parseChunkContent(text), [text])
  if (!segments.length) return <p className="result-text">该切片暂无文本。</p>
  return (
    <div className="rendered-chunk">
      {segments.map((segment, segmentIndex) => (
        segment.type === 'text' ? (
          <p className="result-text" key={`text-${segmentIndex}`}>{segment.value}</p>
        ) : (
          <div className="rendered-table-wrap" key={`table-${segmentIndex}`}>
            <table>
              <tbody>
                {segment.rows.map((row, rowIndex) => (
                  <tr key={rowIndex}>
                    {row.map((cell, cellIndex) => {
                      const Cell = cell.header ? 'th' : 'td'
                      return <Cell key={cellIndex} colSpan={cell.colSpan} rowSpan={cell.rowSpan}>{cell.text}</Cell>
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      ))}
    </div>
  )
}

function parseChunkContent(text: string): ContentSegment[] {
  if (!text.trim()) return []
  const segments: ContentSegment[] = []
  const tablePattern = /<table\b[\s\S]*?<\/table>/gi
  let cursor = 0
  for (const match of text.matchAll(tablePattern)) {
    const index = match.index ?? cursor
    const leading = text.slice(cursor, index).trim()
    if (leading) segments.push({ type: 'text', value: leading })
    const documentNode = new DOMParser().parseFromString(match[0], 'text/html')
    const rows = Array.from(documentNode.querySelectorAll('table tr')).map(row => (
      Array.from(row.children).flatMap(element => {
        if (element.tagName !== 'TD' && element.tagName !== 'TH') return []
        return [{
          text: element.textContent?.trim() || '',
          colSpan: positiveSpan(element.getAttribute('colspan')),
          rowSpan: positiveSpan(element.getAttribute('rowspan')),
          header: element.tagName === 'TH',
        }]
      })
    ))
    if (rows.length) segments.push({ type: 'table', rows })
    cursor = index + match[0].length
  }
  const trailing = text.slice(cursor).trim()
  if (trailing) segments.push({ type: 'text', value: trailing })
  return segments
}

function positiveSpan(value: string | null): number {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : 1
}

function clampTopK(value: number): number {
  if (!Number.isFinite(value)) return 1
  return Math.max(1, Math.min(50, Math.trunc(value)))
}

function shortId(id: string): string {
  return id.length > 12 ? `${id.slice(0, 8)}…` : id
}

function friendlyError(message: string): string {
  if (message.includes('DASHSCOPE_API_KEY')) return '查询向量服务尚未配置 API Key，请先在设置页完成配置。'
  return message
}
