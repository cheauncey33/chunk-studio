import { useEffect, useMemo, useRef, useState } from 'react'
import { FileText, FileWarning, GitBranch, Loader2 } from 'lucide-react'
import { api, type AuditReportDetail } from '@/api'
import { AuditProjectGroupCard, AuditResultCard } from '@/components/audit-result-card'
import { CaseWorkflowTrace } from '@/components/case-workflow-trace'
import { ReportPagePreview } from '@/components/report-page-preview'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  PROBLEM_FILTERS,
  type ProblemFilter,
  caseJudgmentStatus,
  caseProjectName,
  caseRequirementText,
  formatAuditTime,
} from '@/lib/audit-status'
import { cn } from '@/lib/utils'

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

type RightPane = 'report' | 'workflow'

const filterActiveClass: Record<ProblemFilter, string> = {
  mismatch: 'bg-[#fff1f0] font-medium text-[#b91c1c]',
  insufficient_context: 'bg-[#fff7ed] font-medium text-[#b45309]',
  not_audited: 'bg-[#f3f4f6] font-medium text-[#4b5563]',
}

type CaseListItem =
  | { type: 'single'; key: string; item: Record<string, unknown>; index: number }
  | {
      type: 'project_group'
      key: string
      project: string
      members: Array<{ item: Record<string, unknown>; index: number }>
    }

export function AuditResultViewer({
  open,
  reportName,
  onOpenChange,
}: {
  open: boolean
  reportName: string | null
  onOpenChange: (open: boolean) => void
}) {
  const [detail, setDetail] = useState<AuditReportDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [filter, setFilter] = useState<ProblemFilter>('mismatch')
  const [selectedCaseId, setSelectedCaseId] = useState('')
  const [rightPane, setRightPane] = useState<RightPane>('report')
  const [page, setPage] = useState(1)
  const [pageCount, setPageCount] = useState(0)
  const [locating, setLocating] = useState(false)
  const [expandedProjects, setExpandedProjects] = useState<Record<string, boolean>>({})
  const [flashTokens, setFlashTokens] = useState<Record<string, number>>({})
  const locateCacheRef = useRef<Map<string, number | null>>(new Map())

  const selectCase = (caseId: string) => {
    setSelectedCaseId(caseId)
    setFlashTokens(prev => ({ ...prev, [caseId]: (prev[caseId] || 0) + 1 }))
    setRightPane('report')
  }

  useEffect(() => {
    if (!open || !reportName) {
      setDetail(null)
      setError('')
      setSelectedCaseId('')
      setFilter('mismatch')
      setRightPane('report')
      setPage(1)
      setPageCount(0)
      setExpandedProjects({})
      setFlashTokens({})
      locateCacheRef.current = new Map()
      return
    }
    let cancelled = false
    setLoading(true)
    setError('')
    setRightPane('report')
    api
      .getAuditReport(reportName)
      .then(payload => {
        if (cancelled) return
        setDetail(payload)
      })
      .catch(err => {
        if (cancelled) return
        setError((err as Error).message || '加载审查记录失败')
        setDetail(null)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [open, reportName])

  const cases = useMemo(() => {
    const raw = detail?.payload?.cases
    return Array.isArray(raw) ? raw.filter(isRecord) : []
  }, [detail])

  const filtered = useMemo(() => {
    return cases.filter(item => caseJudgmentStatus(item) === filter)
  }, [cases, filter])

  const listItems = useMemo((): CaseListItem[] => {
    const byProject = new Map<string, Array<{ item: Record<string, unknown>; index: number }>>()
    const projectOrder: string[] = []
    filtered.forEach((item, index) => {
      const key = caseProjectName(item, index)
      const bucket = byProject.get(key)
      if (bucket) {
        bucket.push({ item, index })
        return
      }
      byProject.set(key, [{ item, index }])
      projectOrder.push(key)
    })

    return projectOrder.map(key => {
      const members = byProject.get(key) || []
      if (members.length === 1) {
        return {
          type: 'single' as const,
          key: String(members[0].item.case_id || key),
          item: members[0].item,
          index: members[0].index,
        }
      }
      return {
        type: 'project_group' as const,
        key,
        project: key,
        members,
      }
    })
  }, [filtered])

  useEffect(() => {
    if (!filtered.length) {
      setSelectedCaseId('')
      return
    }
    if (!filtered.some(item => String(item.case_id || '') === selectedCaseId)) {
      setSelectedCaseId(String(filtered[0].case_id || ''))
    }
  }, [filtered, selectedCaseId])

  const filterCounts = useMemo(() => {
    const tally: Record<ProblemFilter, number> = {
      mismatch: 0,
      insufficient_context: 0,
      not_audited: 0,
    }
    for (const item of cases) {
      const status = caseJudgmentStatus(item) as ProblemFilter
      if (status in tally) tally[status] += 1
    }
    return tally
  }, [cases])

  const reportFileId = String(detail?.report_file_id || detail?.payload?.report_file_id || '').trim()
  const reportFileName = String(
    detail?.report_file_name
    || detail?.payload?.report_file_name
    || reportName
    || '审查记录',
  )
  const startedAt = detail?.started_at || detail?.payload?.started_at

  const selectedIndex = filtered.findIndex(item => String(item.case_id || '') === selectedCaseId)
  const selected = selectedIndex >= 0 ? filtered[selectedIndex] : null

  useEffect(() => {
    if (!reportFileId) {
      setPageCount(0)
      return
    }
    let cancelled = false
    api
      .getFile(reportFileId)
      .then(file => {
        if (cancelled) return
        setPageCount(file.page_count || 0)
        setPage(prev => Math.min(Math.max(prev, 1), Math.max(file.page_count || 1, 1)))
      })
      .catch(() => {
        if (!cancelled) setPageCount(0)
      })
    return () => {
      cancelled = true
    }
  }, [reportFileId])

  useEffect(() => {
    if (!reportFileId || !selected || selectedIndex < 0) return
    const caseId = `v2:${String(selected.case_id || selectedIndex)}`
    const project = caseProjectName(selected, selectedIndex)
    const requirement = caseRequirementText(selected)
    const jump = (targetPage: number | null) => {
      if (targetPage == null) return
      setPage(targetPage)
      setRightPane('report')
    }

    const cached = locateCacheRef.current.get(caseId)
    if (cached !== undefined) {
      jump(cached)
      return
    }

    if (!project.trim() && !requirement.trim()) return

    let cancelled = false
    setLocating(true)
    api
      .locateFileText(reportFileId, '', { project, requirement })
      .then(result => {
        if (cancelled) return
        locateCacheRef.current.set(caseId, result.page)
        if (result.page_count) setPageCount(result.page_count)
        jump(result.page)
      })
      .catch(() => {
        if (!cancelled) locateCacheRef.current.set(caseId, null)
      })
      .finally(() => {
        if (!cancelled) setLocating(false)
      })
    return () => {
      cancelled = true
    }
  }, [reportFileId, selected, selectedCaseId, selectedIndex])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="flex h-[min(980px,97vh)] w-[min(1720px,98vw)] max-w-none flex-col gap-0 overflow-hidden p-0"
        aria-describedby={undefined}
      >
        <DialogHeader className="shrink-0 border-b border-border-button px-5 py-4">
          <DialogTitle className="pr-8 text-[17px]">
            {reportFileName}
          </DialogTitle>
          <DialogDescription className="text-[13px] text-text-secondary">
            {formatAuditTime(typeof startedAt === 'string' || typeof startedAt === 'number' ? startedAt : null)}
            {detail?.assistant_name ? ` · ${detail.assistant_name}` : ''}
            {detail?.knowledge_base_name ? ` · ${detail.knowledge_base_name}` : ''}
            {cases.length ? ` · 共 ${cases.length} 条判定` : ''}
          </DialogDescription>
        </DialogHeader>

        {loading ? (
          <div className="flex flex-1 items-center justify-center gap-2 text-[15px] text-text-secondary">
            <Loader2 className="size-4 animate-spin" />
            加载审查记录…
          </div>
        ) : error ? (
          <div className="flex flex-1 items-center justify-center px-6 text-[15px] text-state-error">
            {error}
          </div>
        ) : (
          <div className="grid min-h-0 flex-1 md:grid-cols-[minmax(22rem,28rem)_minmax(0,1fr)]">
            <aside className="flex min-h-0 flex-col border-b border-border-button bg-[#f8fafc] md:border-b-0 md:border-r">
              <div className="flex shrink-0 gap-1 border-b border-border-button bg-white px-3 py-2">
                {PROBLEM_FILTERS.map(item => (
                  <button
                    key={item.id}
                    type="button"
                    className={cn(
                      'rounded-md px-2.5 py-1.5 text-[13px]',
                      filter === item.id
                        ? filterActiveClass[item.id]
                        : 'text-text-secondary hover:bg-bg-canvas',
                    )}
                    onClick={() => setFilter(item.id)}
                  >
                    {item.label}
                    <span className="ml-1 text-[12px] opacity-70">{filterCounts[item.id]}</span>
                  </button>
                ))}
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto p-2.5">
                {!listItems.length ? (
                  <div className="flex flex-col items-center gap-2 px-4 py-10 text-center text-[14px] text-text-secondary">
                    <FileWarning className="size-5 opacity-60" />
                    当前筛选下没有条目。
                  </div>
                ) : (
                  <ul className="space-y-2.5">
                    {listItems.map(entry => {
                      if (entry.type === 'single') {
                        const caseId = String(entry.item.case_id || entry.index)
                        return (
                          <li key={entry.key}>
                            <AuditResultCard
                              item={entry.item}
                              index={entry.index}
                              flashToken={flashTokens[caseId] || 0}
                              selected={caseId === selectedCaseId}
                              onSelect={() => selectCase(caseId)}
                            />
                          </li>
                        )
                      }

                      const expanded = expandedProjects[entry.key] === true
                      const summaries = entry.members.map(member => (
                        caseRequirementText(member.item)
                        || caseProjectName(member.item, member.index)
                      ))
                      return (
                        <li key={entry.key}>
                          <AuditProjectGroupCard
                            project={entry.project}
                            memberCount={entry.members.length}
                            summaries={summaries}
                            expanded={expanded}
                            tone={filter}
                            onToggle={() => {
                              setExpandedProjects(prev => ({
                                ...prev,
                                [entry.key]: !expanded,
                              }))
                            }}
                          >
                            {entry.members.map(member => {
                              const caseId = String(member.item.case_id || member.index)
                              return (
                                <AuditResultCard
                                  key={caseId}
                                  item={member.item}
                                  index={member.index}
                                  member
                                  flashToken={flashTokens[caseId] || 0}
                                  selected={caseId === selectedCaseId}
                                  onSelect={() => selectCase(caseId)}
                                />
                              )
                            })}
                          </AuditProjectGroupCard>
                        </li>
                      )
                    })}
                  </ul>
                )}
              </div>
            </aside>

            <section className="flex min-h-0 flex-col bg-[#f8fafc]">
              <div className="flex shrink-0 gap-1 border-b border-border-button bg-white px-3 py-2">
                <button
                  type="button"
                  className={cn(
                    'inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[13px]',
                    rightPane === 'report'
                      ? 'bg-[#ecfdfd] font-medium text-[#0f766e]'
                      : 'text-text-secondary hover:bg-bg-canvas',
                  )}
                  onClick={() => setRightPane('report')}
                >
                  <FileText className="size-3.5" />
                  报告页面
                </button>
                <button
                  type="button"
                  className={cn(
                    'inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[13px]',
                    rightPane === 'workflow'
                      ? 'bg-[#ecfdfd] font-medium text-[#0f766e]'
                      : 'text-text-secondary hover:bg-bg-canvas',
                  )}
                  onClick={() => setRightPane('workflow')}
                  disabled={!selectedCaseId || !reportName}
                >
                  <GitBranch className="size-3.5" />
                  工作流
                </button>
              </div>

              <div className="min-h-0 flex-1">
                {rightPane === 'workflow' && reportName && selectedCaseId ? (
                  <CaseWorkflowTrace reportName={reportName} caseId={selectedCaseId} />
                ) : reportFileId ? (
                  <ReportPagePreview
                    fileId={reportFileId}
                    page={page}
                    pageCount={pageCount}
                    locating={locating}
                    onPageChange={setPage}
                  />
                ) : (
                  <div className="flex h-full flex-col items-center justify-center gap-2 px-8 text-center text-[15px] text-text-secondary">
                    <FileWarning className="size-6 opacity-60" />
                    <p>此记录未关联报告 PDF（旧报告或缺 file_id）。</p>
                    <p className="text-[13px]">可切换到「工作流」查看该条目的判定过程；新审查会自动带上 PDF。</p>
                  </div>
                )}
              </div>
            </section>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
