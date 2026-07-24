import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { FileUp, Loader2, Play, RefreshCw } from 'lucide-react'
import { toast } from 'sonner'
import { api, type CSFile, type Job } from '@/api'
import { Badge } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useAssistants, useKnowledgeBases } from '@/hooks/use-knowledge-request'
import { DEFAULT_OIL_ASSISTANT_ID } from '@/lib/assistants'
import { cn } from '@/lib/utils'

const DEFAULT_KB_ID = 'kb_uncategorized'

const STATUS_LABELS: Record<string, string> = {
  supported: '符合',
  mismatch: '不符合',
  insufficient_context: '依据不足',
  not_audited: '未完成审查',
  // Legacy statuses from older reports.
  correct: '符合',
  incorrect: '不符合',
  evidence_not_found: '未找到证据',
  evaluated: '已评测',
  unknown: '待确认',
}

function statusVariant(status: string): 'success' | 'error' | 'secondary' {
  if (status === 'supported' || status === 'correct') return 'success'
  if (status === 'mismatch' || status === 'incorrect') return 'error'
  return 'secondary'
}

type Phase = 'idle' | 'uploading' | 'parsing' | 'ready' | 'running' | 'done' | 'failed'

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function asRecord(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {}
}

function sleep(ms: number) {
  return new Promise(resolve => setTimeout(resolve, ms))
}

async function pollJob(jobId: string, maxAttempts = 180): Promise<Job> {
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    const job = await api.getJob(jobId)
    if (job.status === 'done' || job.status === 'failed') return job
    await sleep(2000)
  }
  throw new Error('审查超时，请稍后到「结果详情」查看')
}

async function waitUntilParsed(fileId: string, maxAttempts = 90): Promise<CSFile> {
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    const hit = await api.getFile(fileId)
    if (hit.parse_ready || hit.parse_status === 'done') return hit
    if (hit.parse_status === 'failed') {
      throw new Error(hit.parse_error || '报告解析失败，请重新上传')
    }
    await sleep(2000)
  }
  throw new Error('报告解析超时，请稍后重试')
}

export default function WorkbenchPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const uploadRef = useRef<HTMLInputElement>(null)
  const { data: assistants = [], isLoading: assistantsLoading } = useAssistants()
  const { data: knowledgeBases = [] } = useKnowledgeBases()
  const [fallbackAssistantId, setFallbackAssistantId] = useState(DEFAULT_OIL_ASSISTANT_ID)
  const [routedLabel, setRoutedLabel] = useState('')

  useEffect(() => {
    api.getSettings().then(payload => {
      const value = String(payload.settings['audit.default_assistant_id'] || '').trim()
      if (value) setFallbackAssistantId(value)
    }).catch(() => {
      /* keep oil default */
    })
  }, [])

  const routableAssistants = useMemo(
    () =>
      assistants.filter(
        item =>
          item.status === 'active'
          && item.active_version
          && item.id !== 'assistant_audit_template'
          && item.knowledge_bases.length > 0,
      ),
    [assistants],
  )

  const fallbackAssistant = useMemo(() => {
    return (
      routableAssistants.find(item => item.id === fallbackAssistantId)
      || routableAssistants.find(item => item.id === DEFAULT_OIL_ASSISTANT_ID)
      || routableAssistants[0]
      || null
    )
  }, [routableAssistants, fallbackAssistantId])

  const knowledgeBaseId = useMemo(() => {
    const bound = fallbackAssistant?.knowledge_bases?.[0]?.id
    if (bound) return bound
    if (knowledgeBases.some(kb => kb.id === DEFAULT_KB_ID)) return DEFAULT_KB_ID
    return knowledgeBases[0]?.id || ''
  }, [fallbackAssistant, knowledgeBases])

  // Session-local reports: history list comes later. Keep current pick + uploads here.
  const [sessionReports, setSessionReports] = useState<CSFile[]>([])
  const reportFiles = useMemo(() => {
    const byId = new Map<string, CSFile>()
    for (const file of sessionReports) byId.set(file.id, file)
    return [...byId.values()]
  }, [sessionReports])

  const [reportFileId, setReportFileId] = useState('')
  const [phase, setPhase] = useState<Phase>('idle')
  const [statusText, setStatusText] = useState('')
  const [reportName, setReportName] = useState(searchParams.get('report') || '')
  const [resultCases, setResultCases] = useState<Array<Record<string, unknown>>>([])
  const [error, setError] = useState('')

  useEffect(() => {
    if (reportFileId && reportFiles.some(file => file.id === reportFileId)) return
    setReportFileId(reportFiles[0]?.id || '')
  }, [reportFiles, reportFileId])

  useEffect(() => {
    const name = searchParams.get('report')
    if (!name) return
    let cancelled = false
    ;(async () => {
      setError('')
      setReportName(name)
      try {
        const detail = await api.getAuditReport(name)
        if (cancelled) return
        const cases = asArray(detail.payload.cases).filter(isRecord)
        setResultCases(cases)
        setPhase('done')
      } catch (err) {
        if (cancelled) return
        setError((err as Error).message)
        setPhase('failed')
      }
    })()
    return () => {
      cancelled = true
    }
  }, [searchParams])

  const selectedReport = reportFiles.find(file => file.id === reportFileId) || null
  const busy = phase === 'uploading' || phase === 'parsing' || phase === 'running'

  const loadResult = async (name: string) => {
    setError('')
    setReportName(name)
    try {
      const detail = await api.getAuditReport(name)
      const cases = asArray(detail.payload.cases).filter(isRecord)
      setResultCases(cases)
      setPhase('done')
    } catch (err) {
      setError((err as Error).message)
      setPhase('failed')
    }
  }

  const onUpload = async (file: File | null) => {
    if (!file) return
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      toast.error('请上传 PDF 报告')
      return
    }
    setError('')
    setPhase('uploading')
    setStatusText('正在上传报告…')
    setResultCases([])
    setReportName('')
    try {
      // Reports are audit inputs only — do not attach to a knowledge base.
      const uploaded = await api.uploadFile(file, { doc_role: 'report', doc_type: 'report' })
      setReportFileId(uploaded.id)
      setSessionReports(prev => [uploaded, ...prev.filter(item => item.id !== uploaded.id)])
      setPhase('parsing')
      setStatusText('正在解析报告，请稍候…')
      const parsed = await waitUntilParsed(uploaded.id)
      setSessionReports(prev => [parsed, ...prev.filter(item => item.id !== parsed.id)])
      setReportFileId(parsed.id)
      setPhase('ready')
      setStatusText('报告已就绪，可以开始审查')
      toast.success('报告已准备好')
    } catch (err) {
      setPhase('failed')
      setError((err as Error).message)
      toast.error((err as Error).message)
    } finally {
      if (uploadRef.current) uploadRef.current.value = ''
    }
  }

  const startAudit = async () => {
    if (!routableAssistants.length) {
      toast.error('还没有可路由的审查配置（请先创建知识库并准备语料）')
      return
    }
    if (!reportFileId) {
      toast.error('请先上传或选择一份报告')
      return
    }

    setError('')
    setPhase('running')
    setStatusText('正在识别报告品类并选择审查配置…')
    setResultCases([])
    setRoutedLabel('')
    try {
      const { route, job } = await api.routeAndRunAssistant({
        report_file_id: reportFileId,
      })
      const chosen = assistants.find(item => item.id === route.assistant_id)
      const kbName =
        chosen?.knowledge_bases?.[0]?.name
        || knowledgeBases.find(kb => kb.id === route.knowledge_base_id)?.name
        || route.knowledge_base_id
      const label = `${chosen?.name || route.assistant_id} · ${kbName}`
      setRoutedLabel(label)
      setStatusText(
        route.fallback_used
          ? `路由兜底：${label}（${route.reason}）。正在审查…`
          : `已路由到「${label}」。正在审查…`,
      )
      const finished = await pollJob(job.id)
      if (finished.status === 'failed') {
        throw new Error(finished.error || '审查失败')
      }
      const name = String((finished.result as Record<string, unknown>)?.report_name || '')
      if (!name) throw new Error('审查完成但未生成结果文件')
      setSearchParams({ report: name })
      await loadResult(name)
      toast.success('审查完成')
    } catch (err) {
      setPhase('failed')
      setError((err as Error).message)
      toast.error((err as Error).message)
    }
  }

  const counts = useMemo(() => {
    const tally: Record<string, number> = {}
    for (const item of resultCases) {
      const judgment = asRecord(item.judgment)
      const status = String(judgment.status || item.evaluation_status || 'unknown')
      tally[status] = (tally[status] || 0) + 1
    }
    return tally
  }, [resultCases])

  return (
    <div className="flex h-full flex-col overflow-auto px-8 py-7">
      <div className="mx-auto flex w-full max-w-7xl min-h-0 flex-1 flex-col gap-6">
        <div>
          <h1 className="text-[18px] font-semibold tracking-tight">审查</h1>
          <p className="mt-1.5 text-[15px] leading-relaxed text-text-secondary">
            上传出厂报告 PDF，点开始审查，直接看结论。标准资料请先放在知识库里。
          </p>
        </div>

        <div className="grid min-h-0 flex-1 gap-5 lg:grid-cols-[minmax(18rem,22rem)_minmax(0,1fr)]">
          <Card className="h-fit border-border-button bg-bg-base shadow-sm">
            <CardHeader>
              <CardTitle className="text-[17px]">1. 准备报告</CardTitle>
              <CardDescription className="text-[15px] leading-relaxed">
                {assistantsLoading
                  ? '加载中…'
                  : routedLabel
                    ? `本次路由：${routedLabel}`
                    : `上传后自动识别品类并路由；兜底「${
                        fallbackAssistant?.name || '未配置'
                      }」· 可路由配置 ${routableAssistants.length} 个`}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <input
                ref={uploadRef}
                type="file"
                accept="application/pdf,.pdf"
                className="hidden"
                onChange={e => void onUpload(e.target.files?.[0] || null)}
              />
              <button
                type="button"
                disabled={busy}
                onClick={() => uploadRef.current?.click()}
                className={cn(
                  'flex w-full flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-border-button bg-bg-canvas px-4 py-8 text-[15px] transition hover:border-accent-primary/50 hover:bg-bg-accent',
                  busy && 'pointer-events-none opacity-60',
                )}
              >
                <FileUp className="size-6 text-accent-primary" />
                <span className="font-medium">上传检测报告 PDF</span>
                <span className="text-sm text-text-secondary">
                  报告只用于本次审查，不会进入知识库语料。标准 / 规范书请放到知识库。
                </span>
              </button>

              <div className="space-y-2">
                <label className="text-[15px] font-medium">本次已上传的报告</label>
                <select
                  className="flex h-11 w-full rounded-lg border border-border-button bg-bg-base px-3 text-[15px]"
                  value={reportFileId}
                  disabled={busy || !reportFiles.length}
                  onChange={e => {
                    setReportFileId(e.target.value)
                    setPhase(e.target.value ? 'ready' : 'idle')
                  }}
                >
                  <option value="">选择检测报告 PDF</option>
                  {reportFiles.map(file => (
                    <option key={file.id} value={file.id}>
                      {file.name}
                    </option>
                  ))}
                </select>
                {!reportFiles.length && (
                  <p className="text-sm text-text-secondary">
                    还没有本次会话报告。历史记录稍后支持；标准证据请到
                    <Link className="mx-1 text-accent-primary hover:underline" to={knowledgeBaseId ? `/kb/${knowledgeBaseId}/files` : '/knowledge-bases'}>
                      知识库
                    </Link>
                    准备。
                  </p>
                )}
              </div>

              {selectedReport && (
                <div className="rounded-lg bg-bg-canvas px-3 py-2 text-sm text-text-secondary">
                  当前检测报告：{selectedReport.name}
                  {selectedReport.page_count ? ` · ${selectedReport.page_count} 页` : ''}
                </div>
              )}

              <Button
                className="w-full text-[15px]"
                size="lg"
                disabled={busy || !reportFileId || routableAssistants.length === 0}
                onClick={() => void startAudit()}
              >
                {phase === 'running' ? (
                  <>
                    <Loader2 className="animate-spin" />
                    审查中…
                  </>
                ) : (
                  <>
                    <Play />
                    开始审查
                  </>
                )}
              </Button>

              {(busy || statusText) && (
                <p className="flex items-center gap-2 text-[15px] text-text-secondary">
                  {busy && <Loader2 className="size-4 animate-spin" />}
                  {statusText}
                </p>
              )}
              {error && <p className="text-[15px] text-state-error">{error}</p>}

              <p className="text-sm text-text-secondary">
                需要调整标准资料或补充约定时，打开
                <Link className="mx-1 text-accent-primary hover:underline" to="/knowledge-bases">
                  知识库
                </Link>
                。路由失败时的兜底助手可在
                <Link className="mx-1 text-accent-primary hover:underline" to="/settings">
                  系统设置
                </Link>
                配置。
              </p>
            </CardContent>
          </Card>

          <Card className="flex min-h-[28rem] flex-col border-border-button bg-bg-base shadow-sm lg:min-h-0">
            <CardHeader className="flex-row items-start justify-between space-y-0">
              <div>
                <CardTitle className="text-[17px]">2. 审查结果</CardTitle>
                <CardDescription className="text-[15px]">
                  {reportName
                    ? `共 ${resultCases.length} 项判定`
                    : '审查完成后，这里只展示结论。'}
                </CardDescription>
              </div>
              {reportName && (
                <div className="flex gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => void loadResult(reportName)}
                  >
                    <RefreshCw className="size-3.5" />
                    刷新
                  </Button>
                  <Button type="button" size="sm" variant="outline" asChild>
                    <Link to={`/runs?report=${encodeURIComponent(reportName)}`}>详情</Link>
                  </Button>
                </div>
              )}
            </CardHeader>
            <CardContent className="flex min-h-0 flex-1 flex-col">
              {!reportName && phase !== 'failed' && (
                <div className="flex flex-1 items-center justify-center rounded-xl border border-dashed border-border-button px-6 py-12 text-center text-[15px] leading-relaxed text-text-secondary">
                  上传报告并开始审查后，结论会出现在这里。
                </div>
              )}

              {reportName && (
                <div className="min-h-0 flex-1 space-y-4 overflow-auto">
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(counts).map(([status, count]) => (
                      <Badge key={status} variant={statusVariant(status)}>
                        {STATUS_LABELS[status] || status} {count}
                      </Badge>
                    ))}
                  </div>

                  <div className="space-y-3">
                    {resultCases.map((item, index) => {
                      const judgment = asRecord(item.judgment)
                      const status = String(judgment.status || item.evaluation_status || 'unknown')
                      const title = String(
                        item.test_item_name
                        || item.item_name
                        || item.title
                        || item.case_id
                        || `检测项 ${index + 1}`,
                      )
                      const reason = String(judgment.reason || item.reason || '').trim()
                      return (
                        <article key={String(item.case_id || index)} className="rounded-xl border border-border-button p-4">
                          <div className="flex flex-wrap items-center gap-2">
                            <Badge variant={statusVariant(status)}>
                              {STATUS_LABELS[status] || status}
                            </Badge>
                            <strong className="text-[15px]">{title}</strong>
                          </div>
                          {reason && (
                            <p className="mt-2 text-[15px] leading-relaxed text-text-secondary">{reason}</p>
                          )}
                        </article>
                      )
                    })}
                    {!resultCases.length && (
                      <p className="text-[15px] text-text-secondary">这份结果里没有检测项列表。</p>
                    )}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
