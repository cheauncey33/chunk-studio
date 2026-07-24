import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, type Chunk, type CSFile } from './api'
import { chunkKind, getBusinessMetadata } from './chunkSchema'
import {
  formatGeneratedAt,
  hasLlmMetadata,
  parseLlmSuggestion,
  shortHash,
  suggestionGeneratedAt,
  suggestionItems,
  suggestionVersion,
  textSha256,
} from './llmMetadata'

interface Props {
  onLocate: (chunk: Chunk) => void
}

type ContentFilter = 'all' | 'table' | 'section'
type Freshness = 'checking' | 'current' | 'changed' | 'unknown'

const CONTENT_FILTERS: Array<{ key: ContentFilter; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'table', label: '表格' },
  { key: 'section', label: '章节' },
]

export function MetadataSuggestionsPage({ onLocate }: Props) {
  const [chunks, setChunks] = useState<Chunk[]>([])
  const [files, setFiles] = useState<CSFile[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [contentFilter, setContentFilter] = useState<ContentFilter>('all')
  const [versionFilter, setVersionFilter] = useState('all')
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [freshness, setFreshness] = useState<Freshness>('unknown')
  const [extracting, setExtracting] = useState(false)
  const [extractMessage, setExtractMessage] = useState('')

  const refresh = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [allChunks, allFiles] = await Promise.all([
        api.listChunks(undefined, { hasLlmSuggestions: true }),
        api.listFiles(),
      ])
      const suggested = allChunks
        .filter(hasLlmMetadata)
        .sort((a, b) => suggestionGeneratedAt(b).localeCompare(suggestionGeneratedAt(a)))
      setChunks(suggested)
      setFiles(allFiles)
      setSelectedId(current => suggested.some(chunk => chunk.id === current) ? current : suggested[0]?.id || '')
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const extractBulk = useCallback(async () => {
    setExtracting(true)
    setError('')
    setExtractMessage('')
    try {
      const summary = await api.extractLlmSuggestionsBulk()
      setExtractMessage(
        summary.eligible === 0
          ? '没有待提取的段落（已有建议的段落会被跳过）。'
          : `已为 ${summary.extracted} / ${summary.eligible} 段生成关键词与问题建议（${summary.prompt_version}）。`,
      )
      await refresh()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setExtracting(false)
    }
  }, [refresh])

  const fileNames = useMemo(() => new Map(files.map(file => [file.id, file.name])), [files])
  const versions = useMemo(() => (
    Array.from(new Set(chunks.map(suggestionVersion).filter(Boolean))).sort().reverse()
  ), [chunks])
  const filteredChunks = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase()
    return chunks.filter(chunk => {
      if (contentFilter !== 'all' && chunkKind(chunk) !== contentFilter) return false
      const version = suggestionVersion(chunk)
      if (versionFilter === 'legacy' && version) return false
      if (versionFilter !== 'all' && versionFilter !== 'legacy' && version !== versionFilter) return false
      if (!normalizedQuery) return true
      const keywords = suggestionItems(parseLlmSuggestion(chunk.metadata_llm?.keywords).value)
      const questions = suggestionItems(parseLlmSuggestion(chunk.metadata_llm?.questions).value)
      const haystack = [
        fileNames.get(chunk.file_id) || '',
        chunk.text || '',
        ...keywords,
        ...questions,
      ].join('\n').toLocaleLowerCase()
      return haystack.includes(normalizedQuery)
    })
  }, [chunks, contentFilter, fileNames, query, versionFilter])
  const selected = filteredChunks.find(chunk => chunk.id === selectedId) || filteredChunks[0] || null

  useEffect(() => {
    let cancelled = false
    const sourceHash = selected
      ? parseLlmSuggestion(selected.metadata_llm?.questions).sourceTextSha256
        || parseLlmSuggestion(selected.metadata_llm?.keywords).sourceTextSha256
      : ''
    if (!selected || !sourceHash || !crypto.subtle) {
      setFreshness('unknown')
      return
    }
    setFreshness('checking')
    textSha256(selected.text || '')
      .then(hash => { if (!cancelled) setFreshness(hash === sourceHash ? 'current' : 'changed') })
      .catch(() => { if (!cancelled) setFreshness('unknown') })
    return () => { cancelled = true }
  }, [selected])

  const filesWithSuggestions = new Set(chunks.map(chunk => chunk.file_id)).size
  const completeCount = chunks.filter(chunk => (
    suggestionItems(parseLlmSuggestion(chunk.metadata_llm?.keywords).value).length > 0
    && suggestionItems(parseLlmSuggestion(chunk.metadata_llm?.questions).value).length > 0
  )).length
  const legacyCount = chunks.filter(chunk => !suggestionVersion(chunk)).length

  return (
    <main className="legacy-surface metadata-review-page">
      <div className="metadata-review-shell">
        <header className="metadata-review-heading">
          <div>
            <h2>AI 建议审核</h2>
            <p>查看 AI 给内容片段写的关键词、可回答问题。这里只看建议；点「定位」可去编辑器核对原文后再决定是否采纳。</p>
          </div>
          <div className="metadata-review-heading-actions">
            <button type="button" className="primary" onClick={extractBulk} disabled={extracting || loading}>
              {extracting ? '提取中…' : '批量提取建议'}
            </button>
            <button type="button" onClick={refresh} disabled={loading || extracting}>{loading ? '刷新中' : '刷新'}</button>
          </div>
        </header>

        {extractMessage && <p className="metadata-extract-message">{extractMessage}</p>}

        <div className="metadata-review-stats">
          <div><strong>{chunks.length}</strong><span>含建议的段落</span></div>
          <div><strong>{completeCount}</strong><span>关键词 + 问题都有</span></div>
          <div><strong>{filesWithSuggestions}</strong><span>覆盖文件</span></div>
          <div><strong>{legacyCount}</strong><span>旧版未标记</span></div>
        </div>

        <div className="metadata-review-toolbar">
          <div className="segmented" aria-label="内容类型筛选">
            {CONTENT_FILTERS.map(item => (
              <button
                key={item.key}
                type="button"
                className={contentFilter === item.key ? 'on' : ''}
                onClick={() => setContentFilter(item.key)}
              >
                {item.label}
              </button>
            ))}
          </div>
          <select value={versionFilter} onChange={event => setVersionFilter(event.target.value)} aria-label="Prompt 版本">
            <option value="all">全部版本</option>
            {versions.map(version => <option key={version} value={version}>{version}</option>)}
            {legacyCount > 0 && <option value="legacy">未标记版本</option>}
          </select>
          <input
            value={query}
            onChange={event => setQuery(event.target.value)}
            placeholder="搜索文件、正文、关键词或问题"
            aria-label="搜索 LLM 元数据"
          />
          <span className="metadata-review-count">{filteredChunks.length} 条</span>
        </div>

        {error && <div className="search-error" role="alert"><strong>加载失败</strong><span>{error}</span></div>}

        <div className="metadata-review-layout">
          <aside className="metadata-review-list">
            {filteredChunks.map(chunk => {
              const keywords = suggestionItems(parseLlmSuggestion(chunk.metadata_llm?.keywords).value)
              const version = suggestionVersion(chunk)
              return (
                <button
                  type="button"
                  key={chunk.id}
                  className={selected?.id === chunk.id ? 'selected' : ''}
                  onClick={() => setSelectedId(chunk.id)}
                >
                  <span className="metadata-review-list-head">
                    <b>{fileNames.get(chunk.file_id) || chunk.file_id}</b>
                    <span>P{chunk.page}</span>
                  </span>
                  <span className="metadata-review-list-meta">
                    <span>{chunkKind(chunk) === 'table' ? '表格' : chunkKind(chunk) === 'section' ? '章节' : chunkKind(chunk)}</span>
                    <span>{version || '旧版'}</span>
                  </span>
                  <span className="metadata-review-list-preview">{keywords.slice(0, 4).join(' · ') || '无关键词'}</span>
                </button>
              )
            })}
            {!loading && filteredChunks.length === 0 && <p className="muted metadata-review-empty">没有符合条件的建议。</p>}
          </aside>

          <section className="metadata-review-detail">
            {selected ? (
              <SuggestionDetail
                chunk={selected}
                fileName={fileNames.get(selected.file_id) || selected.file_id}
                freshness={freshness}
                onLocate={() => onLocate(selected)}
              />
            ) : (
              <div className="metadata-review-empty muted">选择一条建议查看详情。</div>
            )}
          </section>
        </div>
      </div>
    </main>
  )
}

function SuggestionDetail({
  chunk,
  fileName,
  freshness,
  onLocate,
}: {
  chunk: Chunk
  fileName: string
  freshness: Freshness
  onLocate: () => void
}) {
  const keywords = parseLlmSuggestion(chunk.metadata_llm?.keywords)
  const questions = parseLlmSuggestion(chunk.metadata_llm?.questions)
  const keywordItems = suggestionItems(keywords.value)
  const questionItems = suggestionItems(questions.value)
  const provenance = questions.promptVersion ? questions : keywords
  const business = getBusinessMetadata(chunk)
  const title = String(business.table_title || business.section_title || '')

  return (
    <>
      <header className="metadata-detail-head">
        <div>
          <span className="metadata-detail-kicker">{fileName} · 第 {chunk.page} 页</span>
          <h3>{title || `${chunkKind(chunk) === 'table' ? '表格' : '章节'}切片`}</h3>
        </div>
        <button type="button" className="primary" onClick={onLocate}>在文档中定位</button>
      </header>

      <div className="metadata-detail-source">
        <h4>原始切片</h4>
        <pre>{chunk.text || '无文本'}</pre>
      </div>

      <section className="metadata-detail-section">
        <div className="metadata-detail-section-head">
          <h4>关键词</h4>
          <span className={`suggestion-status ${keywords.status}`}>{statusLabel(keywords.status)}</span>
        </div>
        <div className="metadata-keywords">
          {keywordItems.map(item => <span key={item}>{item}</span>)}
          {!keywordItems.length && <span className="muted">没有关键词</span>}
        </div>
      </section>

      <section className="metadata-detail-section">
        <div className="metadata-detail-section-head">
          <h4>可回答问题</h4>
          <span className={`suggestion-status ${questions.status}`}>{statusLabel(questions.status)}</span>
        </div>
        {questionItems.length ? (
          <ol className="metadata-questions">
            {questionItems.map(item => <li key={item}>{item}</li>)}
          </ol>
        ) : <p className="muted">旧版记录没有问题字段。</p>}
      </section>

      <dl className="metadata-provenance">
        <div><dt>Prompt 版本</dt><dd>{provenance.promptVersion || '未标记（旧版）'}</dd></div>
        <div><dt>模型</dt><dd>{provenance.model || '未记录'}</dd></div>
        <div><dt>生成时间</dt><dd>{formatGeneratedAt(provenance.generatedAt)}</dd></div>
        <div><dt>正文一致性</dt><dd className={freshness}>{freshnessLabel(freshness)}</dd></div>
        <div><dt>正文哈希</dt><dd className="mono" title={provenance.sourceTextSha256}>{shortHash(provenance.sourceTextSha256)}</dd></div>
      </dl>
    </>
  )
}

function statusLabel(status: string): string {
  return status === 'accepted' ? '已采纳' : status === 'rejected' ? '已拒绝' : '建议'
}

function freshnessLabel(freshness: Freshness): string {
  if (freshness === 'checking') return '校验中'
  if (freshness === 'current') return '与当前正文一致'
  if (freshness === 'changed') return '正文已修改，建议可能过期'
  return '无法校验'
}
