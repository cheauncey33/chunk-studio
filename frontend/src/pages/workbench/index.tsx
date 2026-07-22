import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { FileUp, Loader2, Play, RefreshCw } from 'lucide-react'
import { toast } from 'sonner'
import { api, type CSFile, type Job } from '@/api'
import { Badge } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useAssistants, useKbFiles, useKnowledgeBases } from '@/hooks/use-knowledge-request'
import { cn } from '@/lib/utils'

const DEFAULT_ASSISTANT_ID = 'assistant_oil_transformer_audit'
const DEFAULT_KB_ID = 'kb_uncategorized'

const STATUS_LABELS: Record<string, string> = {
  correct: '符合',
  incorrect: '不符合',
  insufficient_context: '依据不足',
  evidence_not_found: '未找到证据',
  evaluated: '已评测',
  unknown: '待确认',
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

/** Files meant to be audited (factory/test reports), not standards in the KB. */
function isAuditReportFile(file: Pick<CSFile, 'name' | 'metadata'>): boolean {
  const meta = isRecord(file.metadata) ? file.metadata : {}
  const kind = String(meta.doc_role || meta.doc_type || '').toLowerCase()
  if (kind === 'report') return true
  if (kind === 'standard' || kind === 'spec' || kind === 'naming' || kind === 'reference') {
    return false
  }

  const name = file.name || ''
  if (/报告|出厂|检测报告|试验报告|检验报告|型式试验|HBJC/i.test(name)) return true

  // National / industry standards and similar corpus docs.
  if (/^(GB\/?T?|GBZ|JB\/?T?|JBT|Q\/?\s*GDW|DL\/?T?|NB\/?T?|IEC|ISO)[\s\/\-._]/i.test(name)) {
    return false
  }
  if (/技术规范|技术条件|技术要求|编制方法|试验导则|标准\b/.test(name) && !/报告/.test(name)) {
    return false
  }
  return false
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

  const assistant = useMemo(() => {
    return (
      assistants.find(item => item.id === DEFAULT_ASSISTANT_ID)
      || assistants.find(item => item.status === 'active')
      || assistants[0]
      || null
    )
  }, [assistants])

  const knowledgeBaseId = useMemo(() => {
    const bound = assistant?.knowledge_bases?.[0]?.id
    if (bound) return bound
    if (knowledgeBases.some(kb => kb.id === DEFAULT_KB_ID)) return DEFAULT_KB_ID
    return knowledgeBases[0]?.id || ''
  }, [assistant, knowledgeBases])

  const { data: files = [], isLoading: filesLoading } = useKbFiles(knowledgeBaseId || undefined)

  const readyFiles = useMemo(
    () => files.filter(file => file.parse_ready || file.parse_status === 'done'),
    [files],
  )
  // Session-local reports: history list comes later. Keep current pick + uploads here.
  const [sessionReports, setSessionReports] = useState<CSFile[]>([])
  const reportFiles = useMemo(() => {
    const byId = new Map<string, CSFile>()
    for (const file of sessionReports) byId.set(file.id, file)
    return [...byId.values()]
  }, [sessionReports])
  const evidenceReadyCount = useMemo(
    () => readyFiles.filter(file => !isAuditReportFile(file)).length,
    [readyFiles],
  )

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
    if (!assistant?.id) {
      toast.error('尚未配置审查助手')
      return
    }
    if (!assistant.active_version) {
      toast.error('审查助手还没有启用版本')
      return
    }
    if (!assistant.knowledge_bases?.length) {
      toast.error('请先在高级设置里给助手绑定知识库')
      return
    }
    if (!reportFileId) {
      toast.error('请先上传或选择一份报告')
      return
    }
    if (evidenceReadyCount < 1) {
      toast.error('知识库里还需要有标准等证据文件，不能只有报告本身')
      return
    }

    setError('')
    setPhase('running')
    setStatusText('正在审查，通常需要几分钟…')
    setResultCases([])
    try {
      const job = await api.startAssistantRun(assistant.id, {
        report_file_id: reportFileId,
      })
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
                {assistantsLoading || filesLoading
                  ? '加载中…'
                  : `使用「${assistant?.name || '未配置助手'}」· 资料库「${
                      assistant?.knowledge_bases?.[0]?.name
                      || knowledgeBases.find(kb => kb.id === knowledgeBaseId)?.name
                      || '—'
                    }」`}
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
                disabled={busy || !reportFileId || !assistant}
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
                。流程细节在
                <Link className="mx-1 text-accent-primary hover:underline" to="/assistants">
                  助手
                </Link>
                。
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
                      <Badge
                        key={status}
                        variant={status === 'correct' ? 'success' : status === 'incorrect' ? 'error' : 'secondary'}
                      >
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
                            <Badge
                              variant={
                                status === 'correct'
                                  ? 'success'
                                  : status === 'incorrect'
                                    ? 'error'
                                    : 'secondary'
                              }
                            >
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
