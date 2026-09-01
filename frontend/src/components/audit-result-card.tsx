import { useEffect, useState, type ReactNode } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import {
  caseEvidenceList,
  caseAuthorityLabel,
  caseJudgmentReason,
  caseJudgmentStatus,
  caseProjectName,
  caseReportUsedValue,
  caseRequirementText,
  caseStandardValueDisplay,
} from '@/lib/audit-status'
import { renderChunkText } from '@/lib/render-chunk-text'
import { cn } from '@/lib/utils'

type ResultTone = 'mismatch' | 'insufficient_context' | 'not_audited'

const toneBorderClass: Record<ResultTone, string> = {
  mismatch: 'border-l-[#ef4444]',
  insufficient_context: 'border-l-[#f59e0b]',
  not_audited: 'border-l-[#9ca3af]',
}

function ValueBlock({
  label,
  value,
}: {
  label: string
  value: string
}) {
  return (
    <div className="min-w-0 rounded-md bg-[#f8fafc] px-2.5 py-2">
      <div className="text-[11px] font-medium leading-none text-[#9ca3af]">
        {label}
      </div>
      <div className="mt-1 line-clamp-3 min-w-0 break-words text-[12px] font-medium leading-snug text-[#111827]">
        {value}
      </div>
    </div>
  )
}

export function AuditProjectGroupCard({
  project,
  memberCount,
  summaries,
  expanded,
  onToggle,
  children,
  tone,
}: {
  project: string
  memberCount: number
  /** Compact lines shown while collapsed (e.g. each requirement). */
  summaries: string[]
  expanded: boolean
  onToggle: () => void
  children: ReactNode
  tone: ResultTone
}) {
  return (
    <div
      className={cn(
        'w-full overflow-hidden rounded-lg border border-[#e5e7eb] border-l-[1.5px] bg-white text-left shadow-[0_1px_2px_rgba(15,23,42,0.04)]',
        toneBorderClass[tone],
      )}
    >
      <button
        type="button"
        className="flex w-full flex-col gap-1.5 px-3 py-2.5 text-left transition-colors hover:bg-[#f8fafc]"
        aria-expanded={expanded}
        onClick={onToggle}
      >
        <div className="flex w-full flex-wrap items-center gap-2">
          <span className="min-w-0 flex-1 text-[13px] font-semibold leading-snug text-[#111827]">
            {project}
          </span>
          <span className="text-[12px] tabular-nums text-[#9ca3af]">
            {memberCount} 项
          </span>
          <span className="inline-flex items-center gap-0.5 text-[11px] leading-none text-[#9ca3af]">
            {expanded ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
            {expanded ? '收起' : '展开'}
          </span>
        </div>
        {!expanded && summaries.length > 0 ? (
          <ul className="w-full space-y-1.5 border-t border-[#f3f4f6] pt-2">
            {summaries.map((line, i) => (
              <li
                key={`${i}-${line.slice(0, 24)}`}
                className="flex gap-1.5 text-[12px] leading-snug text-[#4b5563]"
              >
                <span className="shrink-0 text-[#9ca3af]">{i + 1}.</span>
                <span className="min-w-0 line-clamp-1">{line}</span>
              </li>
            ))}
          </ul>
        ) : null}
      </button>
      {expanded ? (
        <div className="space-y-2 border-t border-[#e5e7eb] bg-[#f8fafc] px-2.5 py-2.5">
          {children}
        </div>
      ) : null}
    </div>
  )
}

export function AuditResultCard({
  item,
  index,
  flashToken = 0,
  onSelect,
  member = false,
  selected = false,
}: {
  item: Record<string, unknown>
  index: number
  /** Bumped on each select so the card can flash again. */
  flashToken?: number
  onSelect: () => void
  /** Peer item inside a project group — title is requirement, not project name. */
  member?: boolean
  selected?: boolean
}) {
  const [evidenceOpen, setEvidenceOpen] = useState(false)
  const [flashing, setFlashing] = useState(false)
  const evidence = caseEvidenceList(item)
  const reportValue = caseReportUsedValue(item)
  const standardValue = caseStandardValueDisplay(item)
  const fullReason = caseJudgmentReason(item)
  const requirement = caseRequirementText(item)
  const projectName = caseProjectName(item, index)
  const status = caseJudgmentStatus(item)
  const authorityLabel = caseAuthorityLabel(item)
  const tone: ResultTone = status === 'mismatch'
    ? 'mismatch'
    : status === 'insufficient_context'
      ? 'insufficient_context'
      : 'not_audited'
  const title = member ? (requirement || projectName) : projectName

  useEffect(() => {
    if (!flashToken) return
    setFlashing(false)
    const frame = window.requestAnimationFrame(() => setFlashing(true))
    const timer = window.setTimeout(() => setFlashing(false), 750)
    return () => {
      window.cancelAnimationFrame(frame)
      window.clearTimeout(timer)
    }
  }, [flashToken])

  return (
    <div
      role="button"
      tabIndex={0}
      className={cn(
        'w-full cursor-pointer border border-[#e5e7eb] border-l-[1.5px] bg-white px-2.5 py-2 text-left shadow-[0_1px_2px_rgba(15,23,42,0.03)] transition',
        toneBorderClass[tone],
        'hover:border-y-[#cbd5e1] hover:border-r-[#cbd5e1] hover:shadow-sm',
        selected && 'border-y-[#14b8a6] border-r-[#14b8a6] bg-[#f0fdfa] shadow-[0_0_0_1px_rgba(20,184,166,0.12)]',
        member ? 'rounded-md' : 'rounded-lg',
        flashing && 'audit-card-flash',
      )}
      onClick={onSelect}
      onKeyDown={event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onSelect()
        }
      }}
    >
      <div className="min-w-0 text-[13px] font-semibold leading-snug text-[#111827]">
        {title}
      </div>
      {authorityLabel ? (
        <span className="mt-1 inline-flex rounded border border-[#e5e7eb] bg-[#f9fafb] px-1.5 py-0.5 text-[11px] font-medium leading-none text-[#6b7280]">
          {authorityLabel}
        </span>
      ) : null}
      {!member && requirement ? (
        <p className="mt-1 line-clamp-2 text-[12px] leading-snug text-[#6b7280]">
          {requirement}
        </p>
      ) : null}

      <div className="mt-2 grid grid-cols-2 gap-1.5">
        <ValueBlock label="报告使用值" value={reportValue} />
        <ValueBlock label="真正标准值" value={standardValue} />
      </div>

      <div className="mt-1.5 border-t border-[#f3f4f6] pt-1.5">
        <div className="text-[11px] font-medium leading-none text-[#9ca3af]">判断依据</div>
        <p className="mt-1 whitespace-pre-wrap text-[12px] leading-snug text-[#4b5563]">
          {fullReason || '暂无判定说明'}
        </p>
      </div>

      <div className="mt-1">
        <button
          type="button"
          className="inline-flex origin-left scale-[0.85] items-center gap-0.5 rounded py-px text-[9px] font-normal leading-none text-[#9ca3af] hover:bg-[#f9fafb] hover:text-[#6b7280]"
          onClick={event => {
            event.stopPropagation()
            setEvidenceOpen(open => !open)
          }}
        >
          {evidenceOpen ? (
            <ChevronDown className="size-2" />
          ) : (
            <ChevronRight className="size-2" />
          )}
          <span>证据</span>
          <span className="tabular-nums">{evidence.length}</span>
        </button>

        {evidenceOpen && (
          <div className="mt-1.5 space-y-1.5" onClick={event => event.stopPropagation()}>
            {!evidence.length ? (
              <p className="rounded-md border border-dashed border-[#e5e7eb] px-2 py-1.5 text-[11px] text-[#9ca3af]">
                无采用证据
              </p>
            ) : (
              evidence.map(entry => {
                const rendered = renderChunkText(entry.text)
                const isTable = /<table[\s>]/i.test(rendered)
                return (
                  <div
                    key={entry.key}
                    className="rounded-md border border-[#e5e7eb] bg-white px-2 py-1.5"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-1.5">
                      <span className="inline-flex max-w-full rounded border border-[#d1d5db] bg-[#f9fafb] px-1.5 py-0.5 text-[11px] font-medium leading-snug text-[#111827]">
                        {entry.citation}
                      </span>
                      {entry.pages ? (
                        <span className="text-[11px] leading-none text-[#9ca3af]">{entry.pages}</span>
                      ) : null}
                    </div>
                    <div
                      className={cn(
                        'legacy-surface rendered-text mt-1.5 text-[11px] leading-snug text-[#374151]',
                        isTable && 'fit-width',
                      )}
                      dangerouslySetInnerHTML={{
                        __html: rendered || '<p class="muted">（无正文）</p>',
                      }}
                    />
                  </div>
                )
              })
            )}
          </div>
        )}
      </div>
    </div>
  )
}
