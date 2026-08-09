import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import {
  api,
  type AuditCaseReview,
  type AuditReportDetail,
  type AuditReportListItem,
  type AuditWorkflowNode,
  type AuditWorkflowTrace,
  type LexicalIndexStatus,
  type ManualKnowledgeRules,
  type RetrievalShadowRun,
} from './api'

type JsonRecord = Record<string, unknown>
type ReportFilter = 'all' | 'end_to_end_audit' | 'retrieval_group_eval' | 'retrieval'

const REPORT_FILTERS: Array<{ key: ReportFilter; label: string; hint: string }> = [
  { key: 'all', label: '全部报告', hint: '显示所有审查/评测报告' },
  {
    key: 'end_to_end_audit',
    label: '完整审查',
    hint: '从头到尾跑一遍：抽参 → 找证据 → 判定对不对',
  },
  {
    key: 'retrieval_group_eval',
    label: '证据召回',
    hint: '只检查「该找的证据有没有找回来」，不做最终判定',
  },
  {
    key: 'retrieval',
    label: '检索命中',
    hint: '只看检索结果列表里有没有命中',
  },
]

const STATUS_LABELS: Record<string, string> = {
  supported: '符合',
  mismatch: '不符合',
  insufficient_context: '上下文不足',
  not_audited: '未完成审查',
  // Legacy statuses from older reports.
  correct: '正确',
  incorrect: '错误',
  evidence_not_found: '未找到证据',
  evaluated: '已评测',
  context_required: '缺上下文',
  confirmed: '已确认',
  corrected: '已纠正',
}

export function AuditPage({
  embedded = false,
  initialReport = '',
  initialCaseId = '',
}: {
  embedded?: boolean
  initialReport?: string
  /** When set, focus this case and open eval panel. */
  initialCaseId?: string
} = {}) {
  const [reports, setReports] = useState<AuditReportListItem[]>([])
  const [selectedReportName, setSelectedReportName] = useState(initialReport)
  const [selectedCaseId, setSelectedCaseId] = useState(initialCaseId)
  const [report, setReport] = useState<AuditReportDetail | null>(null)
  const [reviews, setReviews] = useState<Record<string, AuditCaseReview>>({})
  const [workflow, setWorkflow] = useState<AuditWorkflowTrace | null>(null)
  const [manualRules, setManualRules] = useState<ManualKnowledgeRules | null>(null)
  const [lexicalStatus, setLexicalStatus] = useState<LexicalIndexStatus | null>(null)
  const [shadowRuns, setShadowRuns] = useState<RetrievalShadowRun[]>([])
  const [filter, setFilter] = useState<ReportFilter>('end_to_end_audit')
  const [loadingReports, setLoadingReports] = useState(false)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [loadingWorkflow, setLoadingWorkflow] = useState(false)
  const [error, setError] = useState('')
  const [evalOpen, setEvalOpen] = useState(Boolean(initialCaseId))
  const [shadowOpen, setShadowOpen] = useState(false)

  useEffect(() => {
    if (!initialCaseId) return
    setSelectedCaseId(initialCaseId)
    setEvalOpen(true)
  }, [initialCaseId])

  const refreshReports = useCallback(async () => {
    setLoadingReports(true)
    setError('')
    try {
      const [reportList, rules, indexStatus, shadowRunList] = await Promise.all([
        api.listAuditReports(),
        api.getManualKnowledgeRules().catch(() => null),
        api.getLexicalIndexStatus().catch(() => null),
        api.listRetrievalShadowRuns().catch(() => ({ runs: [] })),
      ])
      setReports(reportList.reports)
      setManualRules(rules)
      setLexicalStatus(indexStatus)
      setShadowRuns(shadowRunList.runs)
      setSelectedReportName(current => {
        if (initialReport && reportList.reports.some(item => item.name === initialReport)) {
          return initialReport
        }
        if (current && reportList.reports.some(item => item.name === current)) return current
        return reportList.reports.find(item => item.kind === 'end_to_end_audit' && !item.parse_error)?.name
          || reportList.reports.find(item => !item.parse_error)?.name
          || reportList.reports[0]?.name
          || ''
      })
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoadingReports(false)
    }
  }, [initialReport])

  useEffect(() => {
    refreshReports()
  }, [refreshReports])

  useEffect(() => {
    if (!selectedReportName) {
      setReport(null)
      return
    }
    const selected = reports.find(item => item.name === selectedReportName)
    if (selected?.parse_error) {
      setReport(null)
      setError(`报告 JSON 无法解析：${selected.parse_error}`)
      return
    }
    setLoadingDetail(true)
    setError('')
    api.getAuditReport(selectedReportName)
      .then(detail => {
        setReport(detail)
        setReviews(detail.reviews || {})
      })
      .catch(err => {
        setReport(null)
        setReviews({})
        setError((err as Error).message)
      })
      .finally(() => setLoadingDetail(false))
  }, [reports, selectedReportName])

  const filteredReports = useMemo(() => {
    if (filter === 'all') return reports
    return reports.filter(item => item.kind === filter)
  }, [filter, reports])

  const cases = useMemo(() => (
    asArray(report?.payload.cases).filter(isRecord)
  ), [report])

  useEffect(() => {
    if (!cases.length) return
    if (cases.some(item => caseId(item) === selectedCaseId)) return
    if (initialCaseId && cases.some(item => caseId(item) === initialCaseId)) {
      setSelectedCaseId(initialCaseId)
      return
    }
    setSelectedCaseId(caseId(cases[0]))
  }, [cases, selectedCaseId, initialCaseId])

  const selectedCase = useMemo(() => (
    cases.find(item => caseId(item) === selectedCaseId) || cases[0] || null
  ), [cases, selectedCaseId])

  const saveReview = useCallback(async (
    reviewCaseId: string,
    body: { status: 'confirmed' | 'corrected'; corrected_status?: string; note?: string },
  ) => {
    if (!report) return
    try {
      const saved = await api.putAuditCaseReview(report.name, reviewCaseId, body)
      setReviews(prev => ({ ...prev, [reviewCaseId]: saved }))
    } catch (err) {
      setError((err as Error).message)
    }
  }, [report])

  const removeReview = useCallback(async (reviewCaseId: string) => {
    if (!report) return
    try {
      await api.deleteAuditCaseReview(report.name, reviewCaseId)
      setReviews(prev => {
        const next = { ...prev }
        delete next[reviewCaseId]
        return next
      })
    } catch (err) {
      setError((err as Error).message)
    }
  }, [report])

  useEffect(() => {
    if (!report || !selectedCaseId || report.kind !== 'end_to_end_audit') {
      setWorkflow(null)
      return
    }
    let active = true
    setLoadingWorkflow(true)
    api.getAuditWorkflow(report.name, selectedCaseId)
      .then(value => {
        if (active) setWorkflow(value)
      })
      .catch(err => {
        if (active) {
          setWorkflow(null)
          setError((err as Error).message)
        }
      })
      .finally(() => {
        if (active) setLoadingWorkflow(false)
      })
    return () => {
      active = false
    }
  }, [report, selectedCaseId])

  return (
    <main className={`legacy-surface audit-page${embedded ? ' embedded' : ''}`}>
      <div className="audit-shell">
        {!embedded && (
          <header className="audit-heading">
            <div>
              <h2>运行记录</h2>
              <p>主视图是流程节点；评测明细默认收起，需要时再展开。</p>
            </div>
            <button type="button" onClick={refreshReports} disabled={loadingReports}>
              {loadingReports ? '刷新中' : '刷新'}
            </button>
          </header>
        )}

        {embedded && (
          <div className="audit-embed-toolbar">
            <p>上方流程是本次审查主视图；下面评测明细默认收起。</p>
            <button type="button" onClick={refreshReports} disabled={loadingReports}>
              {loadingReports ? '刷新中' : '刷新'}
            </button>
          </div>
        )}

        {error && (
          <div className="search-error audit-error" role="alert">
            <strong>加载失败</strong>
            <span>{error}</span>
          </div>
        )}

        {report?.kind === 'end_to_end_audit' && (
          <WorkflowTraceView trace={workflow} loading={loadingWorkflow} />
        )}

        <section className={`audit-collapse${shadowOpen ? ' open' : ''}`}>
          <button
            type="button"
            className="audit-collapse-trigger"
            aria-expanded={shadowOpen}
            onClick={() => setShadowOpen(value => !value)}
          >
            <span>
              <strong>检索旁路监控</strong>
              <small>FTS5 / Shadow 运行状态，调试用</small>
            </span>
            <em>{shadowOpen ? '收起' : '展开'}</em>
          </button>
          {shadowOpen && <ShadowMonitor status={lexicalStatus} runs={shadowRuns} />}
        </section>

        <section className={`audit-collapse${evalOpen ? ' open' : ''}`}>
          <button
            type="button"
            className="audit-collapse-trigger"
            aria-expanded={evalOpen}
            onClick={() => setEvalOpen(value => !value)}
          >
            <span>
              <strong>评测报告明细</strong>
              <small>
                按报告类型查看 case 对错与证据链 · 当前 {filteredReports.length} 份
              </small>
            </span>
            <em>{evalOpen ? '收起' : '展开'}</em>
          </button>

          {evalOpen && (
            <>
              <p className="audit-filter-help">
                <strong>完整审查</strong>：整条链路判定对错；
                <strong>证据召回</strong>：只看证据有没有找对；
                <strong>检索命中</strong>：只看检索结果列表。
              </p>
              <div className="audit-layout">
                <aside className="audit-sidebar">
                  <div className="audit-filter">
                    {REPORT_FILTERS.map(item => (
                      <button
                        key={item.key}
                        type="button"
                        className={filter === item.key ? 'on' : ''}
                        title={item.hint}
                        onClick={() => setFilter(item.key)}
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                  <div className="audit-report-list">
                    {filteredReports.map(item => (
                      <button
                        type="button"
                        key={item.name}
                        className={selectedReportName === item.name ? 'selected' : ''}
                        onClick={() => setSelectedReportName(item.name)}
                      >
                        <span className="audit-report-name">{item.name}</span>
                        <span className="audit-report-meta">
                          {kindLabel(item.kind)} · {item.case_count ?? '—'} 条 · {formatTime(item.modified_at)}
                        </span>
                        {item.parse_error && <span className="audit-report-error">JSON 解析失败</span>}
                      </button>
                    ))}
                    {!filteredReports.length && <div className="search-empty">当前分类下没有报告。</div>}
                  </div>

                  <section className="audit-rules">
                    <h3>人工规则</h3>
                    <p>{manualRules ? `${manualRules.rules?.length ?? 0} 条 · ${manualRules.status || '—'}` : '未加载'}</p>
                    {manualRules?.rules?.map(rule => (
                      <div className="audit-rule" key={String(rule.rule_id)}>
                        <strong>{String(rule.rule_id || 'rule')}</strong>
                        <span>{String(rule.rule_type || '')}</span>
                      </div>
                    ))}
                  </section>
                </aside>

                <section className="audit-main">
                  {loadingDetail && <div className="search-placeholder"><strong>正在加载报告</strong></div>}
                  {!loadingDetail && report && (
                    <>
                      <ReportSummary report={report} />
                      <div className="audit-case-list">
                        {cases.map(item => (
                          <CaseRow
                            key={caseId(item)}
                            item={item}
                            selected={caseId(item) === caseId(selectedCase || {})}
                            onSelect={() => setSelectedCaseId(caseId(item))}
                          />
                        ))}
                        {!cases.length && <div className="search-empty">这个报告没有 case 列表。</div>}
                      </div>
                    </>
                  )}
                  {!loadingDetail && !report && !error && (
                    <div className="search-placeholder">
                      <strong>选择一个报告开始审查</strong>
                      <span>如果没有报告，需要先运行对应 workflow 生成本地 JSON。</span>
                    </div>
                  )}
                </section>

                <aside className="audit-detail">
                  <CaseDetail
                    item={selectedCase}
                    review={selectedCase ? reviews[caseId(selectedCase)] : undefined}
                    onSaveReview={saveReview}
                    onRemoveReview={removeReview}
                  />
                </aside>
              </div>
            </>
          )}
        </section>
      </div>
    </main>
  )
}

function WorkflowTraceView({ trace, loading }: {
  trace: AuditWorkflowTrace | null
  loading: boolean
}) {
  const [selectedNodeId, setSelectedNodeId] = useState('')
  const selectedNode = trace?.nodes.find(node => node.id === selectedNodeId)
    || trace?.nodes[0]
    || null

  useEffect(() => {
    if (!trace?.nodes.length) {
      setSelectedNodeId('')
      return
    }
    if (!trace.nodes.some(node => node.id === selectedNodeId)) {
      setSelectedNodeId(trace.nodes[0].id)
    }
  }, [selectedNodeId, trace])

  if (loading) {
    return <section className="workflow-trace loading"><strong>正在还原流程节点…</strong></section>
  }
  if (!trace || !selectedNode) return null

  return (
    <section className="workflow-trace" aria-label="端到端流程可视化">
      <header className="workflow-trace-head">
        <div>
          <h3>审查流程</h3>
          <p>点击节点查看该次运行的配置、提示词、输入和输出。</p>
        </div>
        <span className={`workflow-source ${trace.trace_source}`}>
          {trace.trace_source === 'recorded' ? '运行时 trace' : '旧报告还原'}
        </span>
      </header>

      {trace.warnings.length > 0 && (
        <div className="workflow-warnings">
          {trace.warnings.map(warning => <span key={warning}>{warning}</span>)}
        </div>
      )}

      <div className="workflow-node-track">
        {trace.nodes.map((node, index) => (
          <div className="workflow-node-step" key={node.id}>
            <button
              type="button"
              className={`workflow-node ${selectedNode.id === node.id ? 'selected' : ''} ${node.kind}`}
              onClick={() => setSelectedNodeId(node.id)}
              aria-pressed={selectedNode.id === node.id}
            >
              <small>{String(index + 1).padStart(2, '0')}</small>
              <strong>{node.label}</strong>
              <span>{nodeKindLabel(node)}</span>
            </button>
            {index < trace.nodes.length - 1 && <i aria-hidden="true">→</i>}
          </div>
        ))}
      </div>

      <NodeInspector node={selectedNode} />
    </section>
  )
}

type InspectorTab = 'config' | 'prompt' | 'input' | 'output'

function NodeInspector({ node }: { node: AuditWorkflowNode }) {
  const [tab, setTab] = useState<InspectorTab>('output')

  useEffect(() => {
    // Prefer the most useful pane for each node kind.
    if (node.kind === 'retrieval') setTab('output')
    else if (node.prompt?.content) setTab('prompt')
    else setTab('output')
  }, [node.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const tabs: Array<{ key: InspectorTab; label: string; hint?: string }> = [
    { key: 'config', label: '配置' },
    {
      key: 'prompt',
      label: '提示词',
      hint: node.prompt
        ? `${node.prompt.path} · ${node.prompt.source === 'recorded_report' ? '报告快照' : '当前仓库'}`
        : undefined,
    },
    { key: 'input', label: '输入' },
    { key: 'output', label: '输出' },
  ]

  return (
    <div className="workflow-inspector">
      <header>
        <div>
          <strong>{node.label}</strong>
          <span>{node.note || '输入输出由报告运行时 trace 记录。'}</span>
        </div>
        {node.diagnostic_only && <em>诊断节点 · 不输入模型</em>}
      </header>

      <div className="workflow-inspector-tabs" role="tablist">
        {tabs.map(item => (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={tab === item.key}
            className={tab === item.key ? 'active' : ''}
            onClick={() => setTab(item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>

      <div className="workflow-inspector-pane" role="tabpanel">
        {tab === 'config' && <ConfigView value={node.configuration} />}
        {tab === 'prompt' && (
          <PromptView
            content={node.prompt?.content || '此节点没有 LLM 提示词。'}
            meta={tabs.find(item => item.key === 'prompt')?.hint}
          />
        )}
        {tab === 'input' && <PayloadView value={node.input} empty="无输入记录" />}
        {tab === 'output' && <PayloadView value={node.output} empty="无输出记录" />}
      </div>
    </div>
  )
}

function ConfigView({ value }: { value: Record<string, unknown> }) {
  const entries = Object.entries(value || {})
  if (!entries.length) {
    return <div className="workflow-empty">无配置</div>
  }
  return (
    <div className="workflow-config-view">
      <dl className="workflow-kv">
        {entries.map(([key, raw]) => (
          <div key={key} className="workflow-kv-row">
            <dt>{key}</dt>
            <dd><code>{formatScalar(raw)}</code></dd>
          </div>
        ))}
      </dl>
      <details className="workflow-raw-details">
        <summary>查看原始 JSON</summary>
        <CodeBlock language="json" value={value} />
      </details>
    </div>
  )
}

function PromptView({ content, meta }: { content: string; meta?: string }) {
  const [mode, setMode] = useState<'render' | 'source'>('render')
  return (
    <div className="workflow-prompt-view">
      <div className="workflow-pane-toolbar">
        {meta && <span className="workflow-meta">{meta}</span>}
        <div className="workflow-mode-switch">
          <button type="button" className={mode === 'render' ? 'active' : ''} onClick={() => setMode('render')}>渲染</button>
          <button type="button" className={mode === 'source' ? 'active' : ''} onClick={() => setMode('source')}>源码</button>
        </div>
      </div>
      {mode === 'render' ? (
        <div className="workflow-markdown">
          <MarkdownLite text={content} />
        </div>
      ) : (
        <CodeBlock language="markdown" value={content} />
      )}
    </div>
  )
}

function PayloadView({ value, empty }: { value: unknown; empty: string }) {
  const [mode, setMode] = useState<'pretty' | 'raw' | 'preview'>('pretty')

  if (value == null || value === '') {
    return <div className="workflow-empty">{empty}</div>
  }

  const record = isPlainObject(value) ? value : null
  const markdownField = record && typeof record.report_markdown === 'string'
    ? record.report_markdown
    : null
  const flatEntries = record && !markdownField && isFlatRecord(record)
    ? Object.entries(record)
    : null

  return (
    <div className="workflow-payload-view">
      <div className="workflow-pane-toolbar">
        <div className="workflow-mode-switch">
          <button type="button" className={mode === 'pretty' ? 'active' : ''} onClick={() => setMode('pretty')}>结构化</button>
          {markdownField && (
            <button type="button" className={mode === 'preview' ? 'active' : ''} onClick={() => setMode('preview')}>正文预览</button>
          )}
          <button type="button" className={mode === 'raw' ? 'active' : ''} onClick={() => setMode('raw')}>JSON</button>
        </div>
        <CopyButton text={stringifyValue(value)} />
      </div>

      {mode === 'raw' && <CodeBlock language="json" value={value} />}

      {mode === 'preview' && markdownField && (
        <div className="workflow-markdown workflow-report-preview">
          <MarkdownLite text={markdownField} />
        </div>
      )}

      {mode === 'pretty' && flatEntries && (
        <dl className="workflow-kv">
          {flatEntries.map(([key, raw]) => (
            <div key={key} className="workflow-kv-row">
              <dt>{key}</dt>
              <dd><code>{formatScalar(raw)}</code></dd>
            </div>
          ))}
        </dl>
      )}

      {mode === 'pretty' && record && markdownField && (
        <div className="workflow-payload-split">
          <dl className="workflow-kv compact">
            {Object.entries(record)
              .filter(([key]) => key !== 'report_markdown')
              .map(([key, raw]) => (
                <div key={key} className="workflow-kv-row">
                  <dt>{key}</dt>
                  <dd><code>{formatScalar(raw)}</code></dd>
                </div>
              ))}
          </dl>
          <div className="workflow-markdown-snippet">
            <header>report_markdown</header>
            <div className="workflow-markdown">
              <MarkdownLite text={markdownField.slice(0, 4000) + (markdownField.length > 4000 ? '\n\n…（已截断，切到「正文预览」看全文）' : '')} />
            </div>
          </div>
        </div>
      )}

      {mode === 'pretty' && !flatEntries && !markdownField && (
        <CodeBlock language="json" value={value} />
      )}
    </div>
  )
}

function CodeBlock({ language, value }: { language: 'json' | 'markdown'; value: unknown }) {
  const text = typeof value === 'string' ? value : stringifyValue(value)
  return (
    <pre className={`workflow-code language-${language}`}>
      {language === 'json' ? <JsonHighlight text={text} /> : text}
    </pre>
  )
}

function JsonHighlight({ text }: { text: string }) {
  const nodes: ReactNode[] = []
  const pattern = /("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g
  let last = 0
  let match: RegExpExecArray | null
  let key = 0
  while ((match = pattern.exec(text)) != null) {
    if (match.index > last) {
      nodes.push(<span key={`t-${key++}`}>{text.slice(last, match.index)}</span>)
    }
    if (match[1] != null) {
      nodes.push(
        <span key={`k-${key++}`} className={match[2] ? 'json-key' : 'json-string'}>
          {match[1]}
        </span>,
      )
      if (match[2]) nodes.push(<span key={`c-${key++}`}>{match[2]}</span>)
    } else if (match[3] != null) {
      nodes.push(<span key={`l-${key++}`} className="json-literal">{match[3]}</span>)
    } else {
      nodes.push(<span key={`n-${key++}`} className="json-number">{match[0]}</span>)
    }
    last = match.index + match[0].length
  }
  if (last < text.length) nodes.push(<span key={`t-${key++}`}>{text.slice(last)}</span>)
  return <>{nodes}</>
}

function MarkdownLite({ text }: { text: string }) {
  return <ReactMarkdown>{text}</ReactMarkdown>
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      type="button"
      className="workflow-copy"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text)
          setCopied(true)
          window.setTimeout(() => setCopied(false), 1200)
        } catch {
          // ignore
        }
      }}
    >
      {copied ? '已复制' : '复制'}
    </button>
  )
}

function stringifyValue(value: unknown): string {
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value ?? null, null, 2)
  } catch {
    return String(value)
  }
}

function formatScalar(value: unknown): string {
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (value == null) return 'null'
  return JSON.stringify(value)
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function isFlatRecord(value: Record<string, unknown>): boolean {
  return Object.values(value).every(item => (
    item == null
    || typeof item === 'string'
    || typeof item === 'number'
    || typeof item === 'boolean'
  ))
}

function nodeKindLabel(node: AuditWorkflowNode): string {
  if (node.diagnostic_only) return 'diagnostic only'
  if (node.kind === 'retrieval') return 'deterministic retrieval'
  return 'DeepSeek'
}

function ShadowMonitor({ status, runs }: {
  status: LexicalIndexStatus | null
  runs: RetrievalShadowRun[]
}) {
  const [selectedId, setSelectedId] = useState('')
  const selected = runs.find(run => run.id === selectedId) || runs[0] || null
  const hits = selected?.payload.lexical_hits || []

  useEffect(() => {
    if (!runs.length) {
      setSelectedId('')
      return
    }
    if (!runs.some(run => run.id === selectedId)) setSelectedId(runs[0].id)
  }, [runs, selectedId])

  return (
    <section className="shadow-monitor">
      <header className="shadow-monitor-head">
        <div>
          <h3>FTS5 检索</h3>
          <span>{status?.tokenizer_version || '未初始化'}</span>
        </div>
        <strong className={status?.enabled ? 'enabled' : 'disabled'}>
          {status?.production_enabled ? '生产双路' : status?.shadow_enabled ? 'Shadow' : '已关闭'}
        </strong>
      </header>
      <div className="shadow-stats">
        <div><strong>{status?.indexed_chunks ?? '—'}</strong><span>已索引</span></div>
        <div><strong>{status?.approved_chunks ?? '—'}</strong><span>approved</span></div>
        <div><strong>{status?.pending_chunks ?? '—'}</strong><span>待同步</span></div>
        <div><strong>{runs.length}</strong><span>近期运行</span></div>
      </div>
      <div className="shadow-monitor-body">
        <div className="shadow-run-list">
          {runs.map(run => (
            <button
              type="button"
              key={run.id}
              className={selected?.id === run.id ? 'selected' : ''}
              onClick={() => setSelectedId(run.id)}
            >
              <span>{run.query}</span>
              <small>
                {run.status} · {run.duration_ms.toFixed(0)} ms · overlap {run.payload.overlap_count ?? 0}
              </small>
            </button>
          ))}
          {!runs.length && <div className="search-empty">暂无 shadow 运行记录</div>}
        </div>
        <div className="shadow-run-detail">
          {!selected && <div className="search-empty">执行一次检索后显示旁路结果</div>}
          {selected && (
            <>
              <div className="shadow-query">
                <strong>{selected.query}</strong>
                <span>{selected.created_at} · {selected.payload.lexical_hit_count ?? 0} lexical hits</span>
                {selected.error && <em>{selected.error}</em>}
              </div>
              <div className="shadow-hit-list">
                {hits.slice(0, 10).map((hit, index) => (
                  <div className="shadow-hit" key={hit.chunk_id}>
                    <strong>{index + 1}</strong>
                    <div>
                      <span>{hit.table_title || hit.section_title || hit.standard_no || hit.file_name}</span>
                      <small>
                        {hit.content_type} · p.{hit.page} · {Object.keys(hit.matched_fields).join(', ')}
                      </small>
                    </div>
                    <code>{hit.rrf_score.toFixed(4)}</code>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </section>
  )
}

function ReportSummary({ report }: { report: AuditReportDetail }) {
  const payload = report.payload
  const summary = asRecord(payload.summary)
  const judgments = asRecord(summary.judgments)
  const statCandidates: Array<[string, unknown]> = [
    ['mode', summary.mode === 'full_report' ? '全量' : summary.mode],
    ['cases', summary.cases],
    ['direct gold', summary.direct_gold_recalled == null ? summary.complete_case_recall : summary.direct_gold_recalled],
    ['符合', judgments.supported ?? judgments.correct],
    ['不符合', judgments.mismatch ?? judgments.incorrect],
    ['上下文不足', judgments.insufficient_context],
    ['未完成审查', judgments.not_audited ?? judgments.evidence_not_found],
  ]
  const stats = statCandidates.filter(([, value]) => value != null)

  return (
    <section className="audit-summary">
      <div className="audit-report-title">
        <div>
          <h3>{report.name}</h3>
          <p>{kindLabel(report.kind)} · {formatBytes(report.size_bytes)} · {formatTime(report.modified_at)}</p>
        </div>
        {payload.retrieval_policy != null && <span>{String(payload.retrieval_policy)}</span>}
      </div>
      <div className="audit-stats">
        {stats.map(([label, value]) => (
          <div key={label}>
            <strong>{display(value)}</strong>
            <span>{label}</span>
          </div>
        ))}
      </div>
    </section>
  )
}

function CaseRow({ item, selected, onSelect }: {
  item: JsonRecord
  selected: boolean
  onSelect: () => void
}) {
  const judgment = asRecord(item.judgment)
  const testItem = asRecord(item.test_item)
  const requirement = asRecord(item.reported_requirement)
  const status = String(judgment.status || item.evaluation_status || 'unknown')
  return (
    <button type="button" className={`audit-case ${selected ? 'selected' : ''}`} onClick={onSelect}>
      <div className="audit-case-head">
        <strong>{caseId(item)}</strong>
        <StatusBadge status={status} />
      </div>
      <span>{String(testItem.project_name || item.group_id || '')}</span>
      <p>{String(requirement.text || '')}</p>
      <div className="audit-case-flags">
        {item.direct_gold_available != null && <span>gold: {displayBool(item.direct_gold_available)}</span>}
        {item.direct_gold_recalled != null && <span>recall: {displayBool(item.direct_gold_recalled)}</span>}
        {item.required_group_count != null && <span>groups: {display(item.recalled_group_count)}/{display(item.required_group_count)}</span>}
      </div>
    </button>
  )
}

function CaseDetail({ item, review, onSaveReview, onRemoveReview }: {
  item: JsonRecord | null
  review?: AuditCaseReview
  onSaveReview?: (
    caseId: string,
    body: { status: 'confirmed' | 'corrected'; corrected_status?: string; note?: string },
  ) => Promise<void> | void
  onRemoveReview?: (caseId: string) => Promise<void> | void
}) {
  if (!item) {
    return <div className="audit-detail-empty">选择一个 case 查看证据链。</div>
  }
  const judgment = asRecord(item.judgment)
  const requirement = asRecord(item.reported_requirement)
  const queries = Object.entries(asRecord(item.queries))
  const evidence = asArray(judgment.evidence).filter(isRecord)
  const groups = asArray(item.groups).filter(isRecord)
  const manualRuleSet = asRecord(item.manual_knowledge_rules)
  const selectedRules = asArray(manualRuleSet.rules).filter(isRecord)
  const status = String(judgment.status || item.evaluation_status || 'unknown')

  return (
    <div className="audit-detail-body">
      <div className="audit-detail-head">
        <div>
          <h3>{caseId(item)}</h3>
          <p>{String(asRecord(item.test_item).project_name || '')}</p>
        </div>
        <StatusBadge status={status} />
      </div>

      <section>
        <h4>报告标准值</h4>
        <p className="audit-requirement">{String(requirement.text || '—')}</p>
      </section>

      {judgment.reason != null && (
        <section>
          <h4>Judge 结论</h4>
          <p>{String(judgment.reason)}</p>
          <div className="audit-case-flags">
            {asArray(judgment.evidence_candidate_keys).map(key => (
              <span key={String(key)}>{String(key)}</span>
            ))}
          </div>
        </section>
      )}

      {onSaveReview && (
        <CaseReviewPanel
          key={caseId(item)}
          caseIdValue={caseId(item)}
          review={review}
          onSave={onSaveReview}
          onRemove={onRemoveReview}
        />
      )}

      {groups.length > 0 && (
        <section>
          <h4>证据组召回</h4>
          {groups.map(group => (
            <div className="audit-group" key={String(group.group_id)}>
              <strong>{String(group.group_id)}</strong>
              <span>{displayBool(group.recalled)}</span>
              {asArray(group.matched_manual_rules).map(rule => (
                <em key={String(rule)}>{String(rule)}</em>
              ))}
            </div>
          ))}
        </section>
      )}

      {selectedRules.length > 0 && (
        <section>
          <h4>本 case 注入的人工规则</h4>
          {selectedRules.map(rule => (
            <details className="audit-evidence" key={String(rule.rule_id)}>
              <summary>{String(rule.rule_id)}</summary>
              <p>{String(rule.rule_text || '')}</p>
            </details>
          ))}
        </section>
      )}

      {queries.length > 0 && (
        <section>
          <h4>查询计划</h4>
          <dl className="audit-query-list">
            {queries.map(([key, value]) => (
              <div key={key}>
                <dt>{key}</dt>
                <dd>{String(value)}</dd>
              </div>
            ))}
          </dl>
        </section>
      )}

      {evidence.length > 0 && (
        <section>
          <h4>候选证据</h4>
          {evidence.map((candidate, index) => (
            <EvidenceItem key={`${String(candidate.candidate_key)}-${index}`} item={candidate} />
          ))}
        </section>
      )}
    </div>
  )
}

const REVIEWABLE_STATUSES = ['supported', 'mismatch', 'insufficient_context', 'not_audited'] as const

function CaseReviewPanel({ caseIdValue, review, onSave, onRemove }: {
  caseIdValue: string
  review?: AuditCaseReview
  onSave: (
    caseId: string,
    body: { status: 'confirmed' | 'corrected'; corrected_status?: string; note?: string },
  ) => Promise<void> | void
  onRemove?: (caseId: string) => Promise<void> | void
}) {
  const [correcting, setCorrecting] = useState(false)
  const [correctedStatus, setCorrectedStatus] = useState<string>(review?.corrected_status || 'supported')
  const [note, setNote] = useState(review?.note || '')
  const [saving, setSaving] = useState(false)

  const run = async (action: () => Promise<void> | void) => {
    setSaving(true)
    try {
      await action()
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="audit-review">
      <h4>人工复核</h4>
      {review && !correcting && (
        <div className="audit-review-current">
          <StatusBadge status={review.status} />
          {review.status === 'corrected' && (
            <span>纠正为 {STATUS_LABELS[review.corrected_status] || review.corrected_status}</span>
          )}
          {review.note && <p>{review.note}</p>}
          <small>{review.updated_at}</small>
        </div>
      )}
      {!correcting ? (
        <div className="audit-review-actions">
          <button
            type="button"
            disabled={saving}
            onClick={() => run(() => onSave(caseIdValue, { status: 'confirmed', note }))}
          >
            {review?.status === 'confirmed' ? '已确认' : '确认判定'}
          </button>
          <button type="button" disabled={saving} onClick={() => setCorrecting(true)}>
            纠正判定
          </button>
          {review && onRemove && (
            <button type="button" disabled={saving} onClick={() => run(() => onRemove(caseIdValue))}>
              撤销复核
            </button>
          )}
        </div>
      ) : (
        <div className="audit-review-form">
          <label>
            正确状态
            <select value={correctedStatus} onChange={e => setCorrectedStatus(e.target.value)}>
              {REVIEWABLE_STATUSES.map(status => (
                <option key={status} value={status}>
                  {STATUS_LABELS[status] || status}
                </option>
              ))}
            </select>
          </label>
          <label>
            备注
            <textarea
              rows={2}
              value={note}
              placeholder="说明纠正原因（可作为后续评测 case 依据）"
              onChange={e => setNote(e.target.value)}
            />
          </label>
          <div className="audit-review-actions">
            <button
              type="button"
              disabled={saving}
              onClick={() =>
                run(async () => {
                  await onSave(caseIdValue, {
                    status: 'corrected',
                    corrected_status: correctedStatus,
                    note,
                  })
                  setCorrecting(false)
                })}
            >
              保存纠正
            </button>
            <button type="button" disabled={saving} onClick={() => setCorrecting(false)}>
              取消
            </button>
          </div>
        </div>
      )}
    </section>
  )
}

function EvidenceItem({ item }: { item: JsonRecord }) {
  const metadata = asRecord(item.business_metadata)
  return (
    <details className="audit-evidence" open>
      <summary>
        <strong>{String(item.candidate_key || 'candidate')}</strong>
        <span>{String(item.content_type || metadata.content_type || '')}</span>
      </summary>
      <dl className="audit-metadata">
        {['standard_no', 'table_no', 'table_title', 'section', 'section_title'].map(key => (
          metadata[key] == null ? null : (
            <div key={key}>
              <dt>{key}</dt>
              <dd>{String(metadata[key])}</dd>
            </div>
          )
        ))}
      </dl>
      <pre>{String(item.text || '').slice(0, 5000)}</pre>
    </details>
  )
}

function StatusBadge({ status }: { status: string }) {
  return <span className={`audit-status ${status}`}>{STATUS_LABELS[status] || status}</span>
}

function asRecord(value: unknown): JsonRecord {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : {}
}

function isRecord(value: unknown): value is JsonRecord {
  return value != null && typeof value === 'object' && !Array.isArray(value)
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function caseId(item: JsonRecord): string {
  return String(item.case_id || item.id || 'case')
}

function display(value: unknown): string {
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(3)
  if (typeof value === 'boolean') return displayBool(value)
  if (value == null || value === '') return '—'
  return String(value)
}

function displayBool(value: unknown): string {
  return value ? 'yes' : 'no'
}

function kindLabel(kind: string): string {
  if (kind === 'end_to_end_audit') return '完整审查'
  if (kind === 'retrieval_group_eval') return '证据召回'
  if (kind === 'retrieval') return '检索命中'
  return '报告'
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

function formatTime(timestamp: number): string {
  if (!Number.isFinite(timestamp)) return '—'
  return new Date(timestamp * 1000).toLocaleString()
}
