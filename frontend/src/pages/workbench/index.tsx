import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, Eye, FileUp, History, Loader2, Moon, Play, RefreshCw, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { api, type AuditJobProgress, type AuditReportListItem, type CSFile, type Job } from '@/api'
import { AuditResultViewer } from '@/components/audit-result-viewer'
import { Explain } from '@/components/explain'
import { Badge, Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useAssistants, useKnowledgeBases } from '@/hooks/use-knowledge-request'
import { DEFAULT_OIL_ASSISTANT_ID } from '@/lib/assistants'
import {
  AUDIT_STATUS_LABELS,
  caseAuthorityLabel,
  caseJudgmentReason,
  caseJudgmentStatus,
  caseProjectName,
  caseReportUsedValue,
  caseStandardValueDisplay,
  formatAuditTime,
  statusVariant,
} from '@/lib/audit-status'
import { helpText } from '@/lib/help-text'
import {
  NIGHT_BATCH_MAX_REPORTS,
  datetimeLocalToAwareIso,
  defaultTonightDatetimeLocal,
  friendlyBatchError,
  isAwareIsoInPast,
  localTimezoneLabelForDatetimeLocal,
} from '@/lib/night-batch'
import { cn } from '@/lib/utils'
import { NightBatchPanel } from './night-batch-panel'

const DEFAULT_KB_ID = 'kb_uncategorized'
/** Kept for later: auto route-and-run via api.routeAndRunAssistant. */
const ENABLE_ASSISTANT_ROUTING = false

type Phase = 'idle' | 'uploading' | 'parsing' | 'ready' | 'running' | 'done' | 'failed'

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function sleep(ms: number) {
  return new Promise(resolve => setTimeout(resolve, ms))
}

function readJobProgress(job: Job): AuditJobProgress | null {
  const progress = job.result?.progress
  return progress && typeof progress === 'object' ? progress : null
}

async function pollJob(
  jobId: string,
  onTick?: (job: Job) => void,
): Promise<Job> {
  // No client-side time limit — full-report audits can run for a long time.
  while (true) {
    const job = await api.getJob(jobId)
    onTick?.(job)
    if (job.status === 'done' || job.status === 'failed') return job
    await sleep(1500)
  }
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

type HistoryGroup = {
  key: string
  fileName: string
  runs: AuditReportListItem[]
}

function historyFileName(item: AuditReportListItem): string {
  const fromReport = String(item.report_file_name || '').trim()
  if (fromReport) return fromReport
  // Prefer a PDF-looking label over the audit JSON report name.
  const raw = String(item.name || '').trim()
  if (/\.pdf$/i.test(raw)) return raw
  return raw || '未命名报告'
}

function groupHistory(reports: AuditReportListItem[]): HistoryGroup[] {
  // Group by display file name so re-uploads (new file_id, same PDF name) merge.
  const groups = new Map<string, HistoryGroup>()
  for (const item of reports) {
    const fileName = historyFileName(item)
    const key = `name:${fileName.toLowerCase()}`
    const existing = groups.get(key)
    if (existing) {
      existing.runs.push(item)
      if (!existing.fileName && fileName) existing.fileName = fileName
    } else {
      groups.set(key, { key, fileName, runs: [item] })
    }
  }
  const ordered = [...groups.values()]
  for (const group of ordered) {
    group.runs.sort((a, b) => {
      const ta = String(a.started_at || a.finished_at || a.modified_at || '')
      const tb = String(b.started_at || b.finished_at || b.modified_at || '')
      return tb.localeCompare(ta)
    })
  }
  ordered.sort((a, b) => {
    const ta = String(a.runs[0]?.started_at || a.runs[0]?.modified_at || '')
    const tb = String(b.runs[0]?.started_at || b.runs[0]?.modified_at || '')
    return tb.localeCompare(ta)
  })
  return ordered
}

export default function WorkbenchPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const queryClient = useQueryClient()
  const uploadRef = useRef<HTMLInputElement>(null)
  const { data: assistants = [], isLoading: assistantsLoading } = useAssistants()
  const { data: knowledgeBases = [] } = useKnowledgeBases()
  const [defaultAssistantId, setDefaultAssistantId] = useState(DEFAULT_OIL_ASSISTANT_ID)
  const [assistantId, setAssistantId] = useState('')
  const [viewerReport, setViewerReport] = useState<string | null>(null)
  /** Multi-run file groups start expanded; user can collapse. */
  const [collapsedHistoryGroups, setCollapsedHistoryGroups] = useState<Set<string>>(() => new Set())

  useEffect(() => {
    api.getSettings().then(payload => {
      const value = String(payload.settings['audit.default_assistant_id'] || '').trim()
      if (value) setDefaultAssistantId(value)
    }).catch(() => {
      /* keep oil default */
    })
  }, [])

  const selectableAssistants = useMemo(
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

  useEffect(() => {
    if (!selectableAssistants.length) {
      setAssistantId('')
      return
    }
    if (assistantId && selectableAssistants.some(item => item.id === assistantId)) return
    const preferred =
      selectableAssistants.find(item => item.id === defaultAssistantId)
      || selectableAssistants.find(item => item.id === DEFAULT_OIL_ASSISTANT_ID)
      || selectableAssistants[0]
    setAssistantId(preferred.id)
  }, [selectableAssistants, assistantId, defaultAssistantId])

  const selectedAssistant = useMemo(
    () => selectableAssistants.find(item => item.id === assistantId) || null,
    [selectableAssistants, assistantId],
  )

  const knowledgeBaseId = useMemo(() => {
    const bound = selectedAssistant?.knowledge_bases?.[0]?.id
    if (bound) return bound
    if (knowledgeBases.some(kb => kb.id === DEFAULT_KB_ID)) return DEFAULT_KB_ID
    return knowledgeBases[0]?.id || ''
  }, [selectedAssistant, knowledgeBases])

  const historyQuery = useQuery({
    queryKey: ['audit-history'],
    queryFn: () => api.listAuditReports({ history: true }),
  })
  const historyGroups = useMemo(
    () => groupHistory(historyQuery.data?.reports || []),
    [historyQuery.data?.reports],
  )

  const [sessionReports, setSessionReports] = useState<CSFile[]>([])
  const reportFiles = useMemo(() => {
    const byId = new Map<string, CSFile>()
    for (const file of sessionReports) byId.set(file.id, file)
    return [...byId.values()]
  }, [sessionReports])

  const [selectedReportIds, setSelectedReportIds] = useState<string[]>([])
  const [phase, setPhase] = useState<Phase>('idle')
  const [statusText, setStatusText] = useState('')
  const [reportName, setReportName] = useState(searchParams.get('report') || '')
  const [resultCases, setResultCases] = useState<Array<Record<string, unknown>>>([])
  const [error, setError] = useState('')
  const [runningFileId, setRunningFileId] = useState('')
  const [auditProgress, setAuditProgress] = useState<AuditJobProgress | null>(null)
  const [batchProgress, setBatchProgress] = useState<{ index: number; total: number } | null>(null)
  const [scheduledAtLocal, setScheduledAtLocal] = useState(() => defaultTonightDatetimeLocal())
  const [scheduling, setScheduling] = useState(false)

  useEffect(() => {
    setSelectedReportIds(prev => prev.filter(id => reportFiles.some(file => file.id === id)))
  }, [reportFiles])

  const loadResult = useCallback(async (name: string) => {
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
  }, [])

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

  const selectedReports = useMemo(
    () => reportFiles.filter(file => selectedReportIds.includes(file.id)),
    [reportFiles, selectedReportIds],
  )
  const rightPanel = searchParams.get('panel') === 'batches' && !reportName ? 'batches' : 'history'
  const selectedBatchId = rightPanel === 'batches' ? (searchParams.get('batch') || null) : null
  const busy = phase === 'uploading' || phase === 'parsing' || phase === 'running' || scheduling

  const filesQuery = useQuery({
    queryKey: ['files'],
    queryFn: () => api.listFiles(),
    enabled: rightPanel === 'batches',
    staleTime: 60_000,
  })
  const fileNameById = useMemo(() => {
    const map = new Map<string, string>()
    for (const file of filesQuery.data || []) map.set(file.id, file.name)
    for (const file of sessionReports) map.set(file.id, file.name)
    return map
  }, [filesQuery.data, sessionReports])
  const assistantNames = useMemo(() => {
    const map = new Map<string, string>()
    for (const item of selectableAssistants) {
      map.set(item.id, item.knowledge_bases[0]?.name || item.name)
    }
    return map
  }, [selectableAssistants])

  const openHistoryPanel = () => {
    setReportName('')
    setResultCases([])
    setPhase(prev => (prev === 'done' || prev === 'failed' ? 'idle' : prev))
    setSearchParams({})
  }

  const openBatchPanel = (batchId?: string | null) => {
    setReportName('')
    setResultCases([])
    setPhase(prev => (prev === 'done' || prev === 'failed' ? 'idle' : prev))
    const next = new URLSearchParams()
    next.set('panel', 'batches')
    if (batchId) next.set('batch', batchId)
    setSearchParams(next)
  }

  const toggleReportSelected = (fileId: string) => {
    setSelectedReportIds(prev => (
      prev.includes(fileId)
        ? prev.filter(id => id !== fileId)
        : [...prev, fileId]
    ))
    setPhase(prev => (prev === 'idle' || prev === 'done' || prev === 'failed' ? 'ready' : prev))
  }

  const onUpload = async (fileList: FileList | null) => {
    const files = [...(fileList || [])].filter(file => file.name.toLowerCase().endsWith('.pdf'))
    if (!files.length) {
      toast.error('请上传 PDF 报告（可多选）')
      return
    }
    const skipped = (fileList?.length || 0) - files.length
    setError('')
    setPhase('uploading')
    setStatusText(`正在上传 ${files.length} 份报告…`)
    setResultCases([])
    setReportName('')
    const uploadedIds: string[] = []
    try {
      for (let i = 0; i < files.length; i += 1) {
        const file = files[i]
        setStatusText(`正在上传 ${i + 1}/${files.length}：${file.name}`)
        const uploaded = await api.uploadFile(file, { doc_role: 'report', doc_type: 'report' })
        uploadedIds.push(uploaded.id)
        setSessionReports(prev => [uploaded, ...prev.filter(item => item.id !== uploaded.id)])
      }
      setPhase('parsing')
      for (let i = 0; i < uploadedIds.length; i += 1) {
        const id = uploadedIds[i]
        setStatusText(`正在解析 ${i + 1}/${uploadedIds.length}…`)
        const parsed = await waitUntilParsed(id)
        setSessionReports(prev => [parsed, ...prev.filter(item => item.id !== parsed.id)])
      }
      setSelectedReportIds(prev => {
        const next = new Set(prev)
        for (const id of uploadedIds) next.add(id)
        return [...next]
      })
      setPhase('ready')
      setStatusText(
        uploadedIds.length > 1
          ? `已准备 ${uploadedIds.length} 份报告，勾选后可串行审查`
          : '报告已就绪，可以开始审查',
      )
      toast.success(
        skipped > 0
          ? `已上传 ${uploadedIds.length} 份 PDF（跳过 ${skipped} 个非 PDF）`
          : `已上传 ${uploadedIds.length} 份报告`,
      )
    } catch (err) {
      setPhase('failed')
      setError((err as Error).message)
      toast.error((err as Error).message)
    } finally {
      if (uploadRef.current) uploadRef.current.value = ''
    }
  }

  const startAudit = async () => {
    if (!selectableAssistants.length) {
      toast.error('还没有可用的审查助手（请先创建知识库并准备语料）')
      return
    }
    if (!assistantId) {
      toast.error('请选择审查助手')
      return
    }
    if (!selectedReports.length) {
      toast.error('请先勾选至少一份待审查报告')
      return
    }

    setError('')
    setPhase('running')
    setResultCases([])
    setAuditProgress(null)
    setBatchProgress(
      selectedReports.length > 1
        ? { index: 0, total: selectedReports.length }
        : null,
    )
    const finishedNames: string[] = []
    try {
      const chosen = selectableAssistants.find(item => item.id === assistantId)
      const assistantLabel = chosen?.knowledge_bases?.[0]?.name || chosen?.name || assistantId

      for (let i = 0; i < selectedReports.length; i += 1) {
        const file = selectedReports[i]
        setRunningFileId(file.id)
        setAuditProgress(null)
        if (selectedReports.length > 1) {
          setBatchProgress({ index: i + 1, total: selectedReports.length })
        }
        setStatusText(
          selectedReports.length > 1
            ? `串行审查 ${i + 1}/${selectedReports.length}：${file.name}`
            : `使用「${assistantLabel}」审查中…`,
        )

        let job: Job
        if (ENABLE_ASSISTANT_ROUTING) {
          const routed = await api.routeAndRunAssistant({ report_file_id: file.id })
          job = routed.job
        } else {
          job = await api.startAssistantRun(assistantId, { report_file_id: file.id })
        }

        const finished = await pollJob(job.id, next => {
          const progress = readJobProgress(next)
          if (progress) setAuditProgress(progress)
        })
        if (finished.status === 'failed') {
          throw new Error(finished.error || `审查失败：${file.name}`)
        }
        const name = String((finished.result as Record<string, unknown>)?.report_name || '')
        if (!name) throw new Error(`审查完成但未生成结果文件：${file.name}`)
        finishedNames.push(name)
      }

      const lastName = finishedNames[finishedNames.length - 1]
      setSearchParams({ report: lastName })
      await loadResult(lastName)
      await queryClient.invalidateQueries({ queryKey: ['audit-history'] })
      toast.success(
        finishedNames.length > 1
          ? `已串行完成 ${finishedNames.length} 份审查`
          : '审查完成',
      )
    } catch (err) {
      setPhase('failed')
      setError((err as Error).message)
      toast.error((err as Error).message)
      if (finishedNames.length) {
        await queryClient.invalidateQueries({ queryKey: ['audit-history'] })
      }
    } finally {
      setRunningFileId('')
      setAuditProgress(null)
      setBatchProgress(null)
    }
  }

  const scheduleNightBatch = async () => {
    if (!selectableAssistants.length) {
      toast.error('还没有可用的审查助手（请先创建知识库并准备语料）')
      return
    }
    if (!assistantId) {
      toast.error('请选择审查助手')
      return
    }
    if (!selectedReports.length) {
      toast.error('请先勾选至少一份待审查报告')
      return
    }
    if (selectedReports.length > NIGHT_BATCH_MAX_REPORTS) {
      toast.error(`一次最多预约 ${NIGHT_BATCH_MAX_REPORTS} 份报告`)
      return
    }
    const notReady = selectedReports.filter(
      file => !file.parse_ready && file.parse_status !== 'done',
    )
    if (notReady.length) {
      toast.error(`还有报告未解析完成：${notReady.map(file => file.name).join('、')}`)
      return
    }
    let scheduledAt: string
    try {
      scheduledAt = datetimeLocalToAwareIso(scheduledAtLocal)
    } catch (err) {
      toast.error((err as Error).message)
      return
    }
    if (isAwareIsoInPast(scheduledAt)) {
      toast.error('开始时间已过，请选择今晚或之后的时间')
      return
    }

    setScheduling(true)
    setError('')
    try {
      const created = await api.createAuditBatch({
        assistant_id: assistantId,
        report_file_ids: selectedReports.map(file => file.id),
        scheduled_at: scheduledAt,
      })
      await queryClient.invalidateQueries({ queryKey: ['audit-batches'] })
      openBatchPanel(created.id)
      toast.success(
        `已预约 ${created.total} 份报告，将于 ${formatAuditTime(created.scheduled_at)} 开始`,
      )
    } catch (err) {
      toast.error(friendlyBatchError(err, fileNameById))
    } finally {
      setScheduling(false)
    }
  }

  const counts = useMemo(() => {
    const tally: Record<string, number> = {}
    for (const item of resultCases) {
      const status = caseJudgmentStatus(item)
      tally[status] = (tally[status] || 0) + 1
    }
    return tally
  }, [resultCases])

  const toggleHistoryGroup = (key: string) => {
    setCollapsedHistoryGroups(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const deleteHistoryRun = async (runName: string) => {
    const label = runName.replace(/^end_to_end_audit_/, '')
    if (!window.confirm(`确定删除这次审查记录？\n${label}\n（不会删除源 PDF）`)) return
    try {
      await api.deleteAuditReport(runName)
      if (viewerReport === runName) setViewerReport(null)
      if (reportName === runName) {
        setReportName('')
        setResultCases([])
        setPhase('idle')
        setSearchParams({})
      }
      await queryClient.invalidateQueries({ queryKey: ['audit-history'] })
      toast.success('已删除审查记录')
    } catch (err) {
      toast.error((err as Error).message || '删除失败')
    }
  }

  return (
    <div className="flex h-full flex-col overflow-auto px-8 py-7">
      <div className="mx-auto flex w-full max-w-7xl min-h-0 flex-1 flex-col gap-6">
        <div>
          <h1 className="text-[18px] font-semibold tracking-tight">审查</h1>
          <p className="mt-1.5 text-[15px] leading-relaxed text-text-secondary">
            可一次上传并勾选多份报告，立即串行审查，或预约夜间批次。右侧可回看历史审查与批次进度。
          </p>
        </div>

        <div className="grid min-h-0 flex-1 gap-5 lg:grid-cols-[minmax(18rem,22rem)_minmax(0,1fr)]">
          <div className="space-y-5">
            <Card className="h-fit border-border-button bg-bg-base shadow-sm">
              <CardHeader>
                <CardTitle className="text-[17px]">1. 准备报告</CardTitle>
                <CardDescription className="text-[15px] leading-relaxed">
                  {assistantsLoading
                    ? '加载中…'
                    : `可多选上传 PDF；手动选择审查助手（可用 ${selectableAssistants.length} 个）。`}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <input
                  ref={uploadRef}
                  type="file"
                  accept="application/pdf,.pdf"
                  multiple
                  className="hidden"
                  onChange={e => void onUpload(e.target.files)}
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
                    支持一次选择多份；仅用于本次审查，不进入知识库。
                  </span>
                </button>

                <div className="space-y-2">
                  <label className="text-[15px] font-medium">审查助手</label>
                  <select
                    className="flex h-11 w-full rounded-lg border border-border-button bg-bg-base px-3 text-[15px]"
                    value={assistantId}
                    disabled={busy || !selectableAssistants.length}
                    onChange={e => setAssistantId(e.target.value)}
                  >
                    {!selectableAssistants.length && <option value="">暂无可用助手</option>}
                    {selectableAssistants.map(item => (
                      <option key={item.id} value={item.id}>
                        {item.knowledge_bases[0]?.name || item.name}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <label className="text-[15px] font-medium">
                      待审查报告
                      {reportFiles.length ? `（已选 ${selectedReports.length}/${reportFiles.length}）` : ''}
                    </label>
                    {reportFiles.length > 1 && (
                      <button
                        type="button"
                        className="text-[12px] text-accent-primary hover:underline disabled:opacity-50"
                        disabled={busy}
                        onClick={() => {
                          setSelectedReportIds(
                            selectedReports.length === reportFiles.length
                              ? []
                              : reportFiles.map(file => file.id),
                          )
                        }}
                      >
                        {selectedReports.length === reportFiles.length ? '取消全选' : '全选'}
                      </button>
                    )}
                  </div>
                  {!reportFiles.length ? (
                    <p className="text-sm text-text-secondary">
                      还没有本次会话报告。标准证据请到
                      <Link className="mx-1 text-accent-primary hover:underline" to={knowledgeBaseId ? `/kb/${knowledgeBaseId}/files` : '/knowledge-bases'}>
                        知识库
                      </Link>
                      准备。
                    </p>
                  ) : (
                    <ul className="max-h-56 space-y-1.5 overflow-y-auto rounded-lg border border-border-button p-2">
                      {reportFiles.map(file => {
                        const checked = selectedReportIds.includes(file.id)
                        const running = runningFileId === file.id
                        return (
                          <li key={file.id}>
                            <label
                              className={cn(
                                'flex cursor-pointer items-start gap-2 rounded-md px-2 py-1.5 text-[13px] hover:bg-bg-canvas',
                                checked && 'bg-bg-canvas',
                                busy && 'pointer-events-none opacity-70',
                              )}
                            >
                              <input
                                type="checkbox"
                                className="mt-0.5 size-3.5 accent-[rgb(var(--accent-primary))]"
                                checked={checked}
                                disabled={busy}
                                onChange={() => toggleReportSelected(file.id)}
                              />
                              <span className="min-w-0 flex-1">
                                <span className="block truncate font-medium text-[#111827]">
                                  {file.name}
                                </span>
                                <span className="mt-0.5 block text-[12px] text-text-secondary">
                                  {file.page_count ? `${file.page_count} 页` : '解析中/待审'}
                                  {running ? ' · 审查中…' : ''}
                                </span>
                              </span>
                              {running ? (
                                <Loader2 className="mt-0.5 size-3.5 shrink-0 animate-spin text-accent-primary" />
                              ) : null}
                            </label>
                          </li>
                        )
                      })}
                    </ul>
                  )}
                </div>

                <Button
                  className="w-full text-[15px]"
                  size="lg"
                  disabled={busy || !selectedReports.length || !assistantId || selectableAssistants.length === 0}
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
                      {selectedReports.length > 1
                        ? `开始审查（${selectedReports.length} 份）`
                        : '开始审查'}
                    </>
                  )}
                </Button>

                <div className="space-y-2 rounded-lg border border-border-button bg-bg-canvas px-3 py-2.5">
                  <div>
                    <Explain text={helpText.workbench.nightBatch} title="预约夜间审查">
                      <span className="text-[15px] font-medium">预约夜间审查</span>
                    </Explain>
                  </div>
                  <div>
                    <Explain text={helpText.workbench.nightStart} title="开始时间">
                      <label className="text-[13px] text-text-secondary" htmlFor="night-batch-start">
                        开始时间（{localTimezoneLabelForDatetimeLocal(scheduledAtLocal)}）
                      </label>
                    </Explain>
                  </div>
                  <Input
                    id="night-batch-start"
                    type="datetime-local"
                    value={scheduledAtLocal}
                    disabled={busy}
                    onChange={e => setScheduledAtLocal(e.target.value)}
                  />
                  <p className="text-[12px] leading-relaxed text-text-secondary">
                    按所选日期在你电脑时区发送，例如 23:00 会带上 {localTimezoneLabelForDatetimeLocal(scheduledAtLocal)}，不依赖服务器时区。
                  </p>
                  <Button
                    type="button"
                    variant="outline"
                    className="w-full text-[15px]"
                    disabled={busy || !selectedReports.length || !assistantId || selectableAssistants.length === 0}
                    onClick={() => void scheduleNightBatch()}
                  >
                    {scheduling ? <Loader2 className="animate-spin" /> : <Moon />}
                    {selectedReports.length > 1
                      ? `预约夜间审查（${selectedReports.length} 份）`
                      : '预约夜间审查'}
                  </Button>
                </div>

                {(busy || statusText || auditProgress) && (
                  <div className="space-y-2 rounded-lg border border-border-button bg-bg-canvas px-3 py-2.5">
                    <p className="flex items-center gap-2 text-[14px] text-text-secondary">
                      {busy && <Loader2 className="size-4 shrink-0 animate-spin" />}
                      <span className="min-w-0 break-words">
                        {statusText
                          || auditProgress?.message
                          || (busy ? '处理中…' : '')}
                      </span>
                    </p>
                    {phase === 'running' && auditProgress ? (
                      <div className="space-y-1.5 text-[13px] text-[#374151]">
                        {batchProgress ? (
                          <div>
                            批次进度：{batchProgress.index}/{batchProgress.total} 份报告
                          </div>
                        ) : null}
                        <div>
                          当前节点：{auditProgress.stage_label || auditProgress.stage || '—'}
                        </div>
                        {auditProgress.case_total != null && auditProgress.case_total > 0 ? (
                          <div>
                            本节点：{auditProgress.case_done ?? 0}/{auditProgress.case_total} 项
                            {auditProgress.project_name
                              ? ` · ${auditProgress.project_name}`
                              : ''}
                          </div>
                        ) : null}
                        {auditProgress.case_label ? (
                          <div className="line-clamp-2 text-[12px] text-text-secondary">
                            {auditProgress.case_label}
                          </div>
                        ) : null}
                        <div className="pt-0.5">
                          <div className="mb-1 flex items-center justify-between text-[12px] text-text-secondary">
                            <span>整份报告</span>
                            <span className="tabular-nums">
                              {Math.max(0, Math.min(100, Number(auditProgress.percent) || 0))}%
                            </span>
                          </div>
                          <div className="h-1.5 overflow-hidden rounded-full bg-[#e5e7eb]">
                            <div
                              className="h-full rounded-full bg-accent-primary transition-[width] duration-500"
                              style={{
                                width: `${Math.max(0, Math.min(100, Number(auditProgress.percent) || 0))}%`,
                              }}
                            />
                          </div>
                        </div>
                      </div>
                    ) : null}
                  </div>
                )}
                {error && <p className="text-[15px] text-state-error">{error}</p>}
              </CardContent>
            </Card>
          </div>

          <Card className="flex min-h-[28rem] flex-col border-border-button bg-bg-base shadow-sm lg:min-h-0">
            <CardHeader className="flex-row items-start justify-between space-y-0">
              <div className="min-w-0">
                {reportName ? (
                  <>
                    <CardTitle className="flex items-center gap-2 text-[17px]">
                      2. 审查结果
                    </CardTitle>
                    <CardDescription className="text-[15px]">
                      共 {resultCases.length} 项判定
                    </CardDescription>
                  </>
                ) : (
                  <>
                    <div className="inline-flex rounded-full bg-[#f3f4f6] p-1 dark:bg-bg-card">
                      <button
                        type="button"
                        className={cn(
                          'inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[13px] font-semibold',
                          rightPanel === 'history'
                            ? 'bg-[#111827] text-white dark:bg-white dark:text-[#111827]'
                            : 'text-[#4b5563] hover:text-[#111827] dark:text-text-secondary',
                        )}
                        onClick={openHistoryPanel}
                      >
                        <History className="size-3.5" />
                        历史审查
                      </button>
                      <button
                        type="button"
                        className={cn(
                          'inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[13px] font-semibold',
                          rightPanel === 'batches'
                            ? 'bg-[#111827] text-white dark:bg-white dark:text-[#111827]'
                            : 'text-[#4b5563] hover:text-[#111827] dark:text-text-secondary',
                        )}
                        onClick={() => openBatchPanel(selectedBatchId)}
                      >
                        <Moon className="size-3.5" />
                        夜间批次
                      </button>
                    </div>
                    {rightPanel === 'batches' ? (
                      <div className="mt-2 text-[15px] text-text-secondary">
                        <Explain text={helpText.workbench.nightList} title="夜间批次">
                          <span>进度、失败报告、Token 与成本按批次汇总。</span>
                        </Explain>
                      </div>
                    ) : (
                      <CardDescription className="mt-2 text-[15px]">
                        相同文件名合并；展开可按时间查看各版本。
                      </CardDescription>
                    )}
                  </>
                )}
              </div>
              <div className="flex gap-2">
                {reportName ? (
                  <>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      onClick={openHistoryPanel}
                    >
                      历史
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      className="bg-[#13c2c2] text-white hover:bg-[#0fb3b3]"
                      onClick={() => setViewerReport(reportName)}
                    >
                      查看本次
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={() => void loadResult(reportName)}
                    >
                      <RefreshCw className="size-3.5" />
                      刷新
                    </Button>
                  </>
                ) : (
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    disabled={rightPanel === 'batches' ? false : historyQuery.isFetching}
                    onClick={() => {
                      if (rightPanel === 'batches') {
                        void queryClient.invalidateQueries({ queryKey: ['audit-batches'] })
                        if (selectedBatchId) {
                          void queryClient.invalidateQueries({ queryKey: ['audit-batch', selectedBatchId] })
                        }
                        return
                      }
                      void historyQuery.refetch()
                    }}
                  >
                    <RefreshCw className={cn('size-3.5', historyQuery.isFetching && 'animate-spin')} />
                  </Button>
                )}
              </div>
            </CardHeader>
            <CardContent className="flex min-h-0 flex-1 flex-col">
              {!reportName && rightPanel === 'batches' && (
                <NightBatchPanel
                  selectedBatchId={selectedBatchId}
                  onSelectBatch={id => openBatchPanel(id)}
                  fileNameById={fileNameById}
                  assistantNames={assistantNames}
                />
              )}
              {!reportName && rightPanel === 'history' && (
                <div className="min-h-0 flex-1 space-y-2.5 overflow-auto">
                  {phase === 'failed' && error ? (
                    <p className="rounded-lg border border-state-error/30 bg-[#fff1f0] px-3 py-2 text-[14px] text-state-error">
                      {error}
                    </p>
                  ) : null}
                  {historyQuery.isLoading && (
                    <p className="flex items-center gap-2 text-[14px] text-text-secondary">
                      <Loader2 className="size-3.5 animate-spin" />
                      加载历史…
                    </p>
                  )}
                  {historyQuery.isError && (
                    <p className="text-[14px] text-state-error">
                      {(historyQuery.error as Error).message || '加载历史失败'}
                    </p>
                  )}
                  {!historyQuery.isLoading && !historyGroups.length && (
                    <div className="flex flex-1 items-center justify-center rounded-xl border border-dashed border-border-button px-6 py-12 text-center text-[15px] leading-relaxed text-text-secondary">
                      暂无历史审查。上传报告并开始审查后，记录会出现在这里。
                    </div>
                  )}
                  {historyGroups.map(group => {
                    const latest = group.runs[0]
                    const multi = group.runs.length > 1
                    const expanded = !multi || !collapsedHistoryGroups.has(group.key)
                    return (
                      <div key={group.key} className="rounded-xl border border-border-button">
                        <button
                          type="button"
                          className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left"
                          onClick={() => {
                            if (multi) toggleHistoryGroup(group.key)
                            else if (latest) setViewerReport(latest.name)
                          }}
                        >
                          {multi ? (
                            expanded
                              ? <ChevronDown className="size-4 shrink-0 text-text-secondary" />
                              : <ChevronRight className="size-4 shrink-0 text-text-secondary" />
                          ) : (
                            <span className="size-4 shrink-0" />
                          )}
                          <div className="min-w-0 flex-1 truncate text-[15px] font-medium text-[#111827]">
                            {group.fileName}
                          </div>
                          <span className="flex shrink-0 items-center gap-1.5">
                            {latest?.case_count != null ? (
                              <Badge variant="secondary">{latest.case_count} 条</Badge>
                            ) : null}
                            {multi ? (
                              <span className="text-[13px] tabular-nums text-text-secondary">
                                {group.runs.length} 个版本
                              </span>
                            ) : null}
                          </span>
                          {!multi ? (
                            <span className="flex shrink-0 items-center gap-1">
                              <Button
                                type="button"
                                size="sm"
                                variant="outline"
                                className="size-8 p-0"
                                title="查看"
                                aria-label="查看"
                                onClick={event => {
                                  event.stopPropagation()
                                  if (latest) setViewerReport(latest.name)
                                }}
                              >
                                <Eye className="size-3.5" />
                              </Button>
                              <Button
                                type="button"
                                size="sm"
                                variant="ghost"
                                className="size-8 p-0 text-state-error hover:bg-[#fff1f0] hover:text-state-error"
                                title="删除"
                                aria-label="删除"
                                onClick={event => {
                                  event.stopPropagation()
                                  if (latest) void deleteHistoryRun(latest.name)
                                }}
                              >
                                <Trash2 className="size-3.5" />
                              </Button>
                            </span>
                          ) : null}
                        </button>
                        {multi && expanded && (
                          <ul className="space-y-1 border-t border-border-button px-3 py-2">
                            {group.runs.map(run => (
                              <li
                                key={run.name}
                                className="flex items-center gap-3 rounded-lg px-2 py-2 hover:bg-bg-canvas"
                              >
                                <span className="min-w-[9.5rem] shrink-0 text-[13px] tabular-nums text-[#374151]">
                                  {formatAuditTime(run.finished_at || run.started_at || run.modified_at)}
                                </span>
                                <span className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
                                  <Badge variant="error">
                                    不符 {Number(run.judgments?.mismatch || 0)}
                                  </Badge>
                                  <Badge variant="success">
                                    符合 {Number(run.judgments?.supported || 0)}
                                  </Badge>
                                  {Number(run.judgments?.insufficient_context || 0) > 0 ? (
                                    <Badge variant="secondary">
                                      依据不足 {Number(run.judgments?.insufficient_context || 0)}
                                    </Badge>
                                  ) : null}
                                  {Number(run.judgments?.not_audited || 0) > 0 ? (
                                    <Badge variant="secondary">
                                      未审查 {Number(run.judgments?.not_audited || 0)}
                                    </Badge>
                                  ) : null}
                                  {run.authority && Number(run.authority.closed_count || 0) + Number(run.authority.model_count || 0) > 0 ? (
                                    <Badge variant="secondary">
                                      程序闭合 {Number(run.authority.closed_count || 0)} · 模型 {Number(run.authority.model_count || 0)}
                                    </Badge>
                                  ) : null}
                                </span>
                                <span className="flex shrink-0 items-center gap-1">
                                  <Button
                                    type="button"
                                    size="sm"
                                    variant="outline"
                                    className="size-8 p-0"
                                    title="查看"
                                    aria-label="查看"
                                    onClick={() => setViewerReport(run.name)}
                                  >
                                    <Eye className="size-3.5" />
                                  </Button>
                                  <Button
                                    type="button"
                                    size="sm"
                                    variant="ghost"
                                    className="size-8 p-0 text-state-error hover:bg-[#fff1f0] hover:text-state-error"
                                    title="删除"
                                    aria-label="删除"
                                    onClick={() => void deleteHistoryRun(run.name)}
                                  >
                                    <Trash2 className="size-3.5" />
                                  </Button>
                                </span>
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}

              {reportName && (
                <div className="min-h-0 flex-1 space-y-4 overflow-auto">
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(counts).map(([status, count]) => (
                      <Badge key={status} variant={statusVariant(status)}>
                        {AUDIT_STATUS_LABELS[status] || status} {count}
                      </Badge>
                    ))}
                  </div>

                  <div className="space-y-3">
                    {resultCases.map((item, index) => {
                      const status = caseJudgmentStatus(item)
                      const title = caseProjectName(item, index)
                      const reason = caseJudgmentReason(item)
                      const authorityLabel = caseAuthorityLabel(item)
                      const reportValue = caseReportUsedValue(item)
                      const standardValue = caseStandardValueDisplay(item)
                      return (
                        <article key={String(item.case_id || index)} className="rounded-xl border border-border-button p-4">
                          <div className="flex flex-wrap items-center gap-2">
                            <Badge variant={statusVariant(status)}>
                              {AUDIT_STATUS_LABELS[status] || status}
                            </Badge>
                            {authorityLabel ? (
                              <Badge variant="secondary">{authorityLabel}</Badge>
                            ) : null}
                            <strong className="text-[15px]">{title}</strong>
                          </div>
                          <div className="mt-3 grid grid-cols-2 gap-2">
                            <div className="min-w-0 rounded-md bg-[#f8fafc] px-2.5 py-2">
                              <div className="text-[11px] font-medium leading-none text-[#9ca3af]">报告使用值</div>
                              <div className="mt-1 break-words text-[13px] font-medium leading-snug text-[#111827]">
                                {reportValue}
                              </div>
                            </div>
                            <div className="min-w-0 rounded-md bg-[#f8fafc] px-2.5 py-2">
                              <div className="text-[11px] font-medium leading-none text-[#9ca3af]">真正标准值</div>
                              <div className="mt-1 break-words text-[13px] font-medium leading-snug text-[#111827]">
                                {standardValue}
                              </div>
                            </div>
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

      <AuditResultViewer
        open={Boolean(viewerReport)}
        reportName={viewerReport}
        onOpenChange={open => {
          if (!open) setViewerReport(null)
        }}
      />
    </div>
  )
}
