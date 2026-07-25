import { useEffect, useMemo, useRef, useState } from 'react'
import type { ManualKnowledgeRules, ParameterSchema } from '@/api'
import { Label, Textarea } from '@/components/ui/input'
import type { QueryPlannerRoute } from '@/lib/query-planner-routes'
import {
  buildBriefPreviewParts,
  buildPromptVarContext,
  buildRuntimePromptSegments,
  stripKnownPlaceholders,
} from '@/lib/prompt-vars'
import type { StepRuleDraft } from '@/lib/step-rules'
import { cn } from '@/lib/utils'

/**
 * Line indexes in `next` that are inserts/replacements.
 * Uses LCS so inserting/removing a middle line (e.g. toggling a route)
 * does not flash every subsequent unchanged line.
 */
function collectChangedLineIndexes(previous: string[], next: string[]): number[] {
  const m = previous.length
  const n = next.length
  if (!n) return []
  if (!m) return next.map((_, index) => index)

  const dp: number[][] = Array.from({ length: m + 1 }, () => Array(n + 1).fill(0))
  for (let i = 1; i <= m; i += 1) {
    for (let j = 1; j <= n; j += 1) {
      dp[i][j] = previous[i - 1] === next[j - 1]
        ? dp[i - 1][j - 1] + 1
        : Math.max(dp[i - 1][j], dp[i][j - 1])
    }
  }

  const matchedNext = new Set<number>()
  let i = m
  let j = n
  while (i > 0 && j > 0) {
    if (previous[i - 1] === next[j - 1]) {
      matchedNext.add(j - 1)
      i -= 1
      j -= 1
    } else if (dp[i - 1][j] >= dp[i][j - 1]) {
      i -= 1
    } else {
      j -= 1
    }
  }

  const changed: number[] = []
  for (let index = 0; index < n; index += 1) {
    if (!matchedNext.has(index)) changed.push(index)
  }
  return changed
}

const BRIEF_PREVIEW_STEPS = new Set([
  'report_parameters',
  'test_items',
  'model_decode',
  'query_planner',
  'audit_judge',
])

export function PromptTemplateEditor({
  stepId,
  value,
  onChange,
  disabled,
  pathHint,
  parameterSchema,
  manualRules,
  kbName,
  kbDescription,
  queryPlannerRoutes,
  stepRules,
  fillHeight = false,
  /** When true, only show runtime preview (left pane owns rules / 判定约定). */
  previewOnly = false,
}: {
  stepId: string
  value: string
  onChange: (next: string) => void
  disabled?: boolean
  pathHint?: string
  parameterSchema?: ParameterSchema | null
  manualRules?: ManualKnowledgeRules | Record<string, unknown> | null
  kbName?: string | null
  kbDescription?: string | null
  queryPlannerRoutes?: QueryPlannerRoute[] | unknown
  /** Per-step human rules from assistant_versions.rules.step_rules. */
  stepRules?: StepRuleDraft[] | null
  /** Stretch preview to fill a split-pane column. */
  fillHeight?: boolean
  previewOnly?: boolean
}) {
  const isBriefPreview = BRIEF_PREVIEW_STEPS.has(stepId)
  const isReportParameters = stepId === 'report_parameters'
  const isQueryPlanner = stepId === 'query_planner'
  const isAuditJudge = stepId === 'audit_judge'
  const [editingStatic, setEditingStatic] = useState(false)
  const [changedLines, setChangedLines] = useState<Set<number>>(() => new Set())
  const briefReady = useRef(false)
  const previousLines = useRef<string[]>([])

  const context = useMemo(
    () =>
      buildPromptVarContext({
        parameterSchema,
        manualRules,
        kbName,
        kbDescription,
        queryPlannerRoutes,
      }),
    [parameterSchema, manualRules, kbName, kbDescription, queryPlannerRoutes],
  )
  const segments = useMemo(
    () => buildRuntimePromptSegments(value, stepId, context),
    [value, stepId, context],
  )
  const briefParts = useMemo(() => {
    if (!isBriefPreview) return []
    return buildBriefPreviewParts(stepId, stripKnownPlaceholders(value), {
      parameterSchema,
      manualRules,
      kbName,
      kbDescription,
      queryPlannerRoutes,
      stepRules,
    })
  }, [
    isBriefPreview,
    stepId,
    value,
    parameterSchema,
    manualRules,
    kbName,
    kbDescription,
    queryPlannerRoutes,
    stepRules,
  ])
  const briefText = useMemo(() => {
    if (!isBriefPreview) return ''
    return briefParts.map(item => item.text).join('\n')
  }, [isBriefPreview, briefParts])
  const briefLines = useMemo(() => (briefText ? briefText.split('\n') : []), [briefText])

  useEffect(() => {
    if (!isBriefPreview) return
    if (!briefReady.current) {
      previousLines.current = briefLines
      briefReady.current = true
      return
    }
    const changed = collectChangedLineIndexes(previousLines.current, briefLines)
    previousLines.current = briefLines
    if (!changed.length) return
    setChangedLines(new Set(changed))
    const timer = window.setTimeout(() => setChangedLines(new Set()), 1100)
    return () => window.clearTimeout(timer)
  }, [briefLines, isBriefPreview])

  useEffect(() => {
    briefReady.current = false
    previousLines.current = []
    setChangedLines(new Set())
    setEditingStatic(false)
  }, [stepId])

  return (
    <div className={cn('space-y-3', fillHeight && 'flex h-full min-h-0 flex-col')}>
      <div className={cn('space-y-2', fillHeight && 'flex min-h-0 flex-1 flex-col')}>
        <div className="flex shrink-0 flex-wrap items-end justify-between gap-2">
          <div>
            <Label className="text-[15px] text-[#6b7280]">本步预览提示词</Label>
            <p className="mt-0.5 text-[12px] text-[#9ca3af]">
              琥珀色为可配置变量，其余为系统框架；左侧改动后即时刷新。
            </p>
          </div>
          {!previewOnly && (
            <button
              type="button"
              disabled={disabled}
              onClick={() => setEditingStatic(open => !open)}
              className="text-[12px] font-medium text-[#0f9f9f] hover:text-[#0b7f7f] disabled:opacity-50"
            >
              {editingStatic ? '收起品类约束' : '编辑品类约束（可选）'}
            </button>
          )}
        </div>

        <div
          className={cn(
            'overflow-auto rounded-xl border border-[#e5e7eb] bg-white px-4 py-3 text-[13px] leading-6 text-[#374151]',
            fillHeight ? 'min-h-0 flex-1' : 'max-h-[22rem]',
            !isBriefPreview && 'space-y-3 bg-[#fafafa] font-mono text-[12px] leading-relaxed',
          )}
        >
          {isBriefPreview ? (
            briefParts.length ? (
              <div>
                {(() => {
                  let lineOffset = 0
                  return briefParts.map((part, partIndex) => {
                    const lines = part.text.split('\n')
                    const start = lineOffset
                    lineOffset += lines.length
                    if (part.kind === 'variable') {
                      return (
                        <div
                          key={`var-${partIndex}-${part.label}`}
                          className="my-1.5 rounded-md border border-[#f59e0b]/40 bg-[#fffbeb] px-2.5 py-2"
                        >
                          <div className="mb-1.5 flex items-center gap-2">
                            <span className="rounded bg-[#d97706] px-1.5 py-0.5 text-[10px] font-semibold tracking-wide text-white">
                              变量
                            </span>
                            <span className="text-[11px] font-medium text-[#92400e]">{part.label}</span>
                          </div>
                          <div className="whitespace-pre-wrap text-[#78350f]">
                            {lines.map((line, index) => {
                              const globalIndex = start + index
                              return (
                                <div
                                  key={`${globalIndex}-${line.slice(0, 24)}`}
                                  className={cn(
                                    'rounded px-1 -mx-1 transition-colors duration-500',
                                    changedLines.has(globalIndex) && 'bg-[#fde68a]',
                                  )}
                                >
                                  {line || '\u00a0'}
                                </div>
                              )
                            })}
                          </div>
                        </div>
                      )
                    }
                    return (
                      <div key={`fw-${partIndex}`} className="whitespace-pre-wrap text-[#4b5563]">
                        {lines.map((line, index) => {
                          const globalIndex = start + index
                          return (
                            <div
                              key={`${globalIndex}-${line.slice(0, 24)}`}
                              className={cn(
                                'rounded px-1 -mx-1 transition-colors duration-500',
                                changedLines.has(globalIndex) && 'bg-[#e5e7eb]',
                              )}
                            >
                              {line || '\u00a0'}
                            </div>
                          )
                        })}
                      </div>
                    )
                  })
                })()}
              </div>
            ) : (
              <span className="text-[#9ca3af]">（空提示词）</span>
            )
          ) : (
            <>
              {segments.map((segment, index) => {
                if (segment.kind === 'static') {
                  const isFrameworkPreamble = segment.text.startsWith('任务场景：')
                  return (
                    <div
                      key={index}
                      className={cn(
                        'whitespace-pre-wrap rounded-md px-2 py-1.5',
                        isFrameworkPreamble
                          ? 'border border-[#e5e7eb] bg-[#f9fafb] text-[#374151]'
                          : 'bg-[#f3f4f6] text-[#4b5563]',
                      )}
                    >
                      <div className="mb-1 font-sans text-[10px] font-semibold tracking-wide text-[#9ca3af]">
                        {isFrameworkPreamble ? '框架 · 任务场景 / 本步任务' : '本步补充规则'}
                      </div>
                      {segment.text}
                    </div>
                  )
                }
                return (
                  <div
                    key={index}
                    className="whitespace-pre-wrap rounded-md px-2.5 py-2"
                    style={{
                      background: '#ccfbf1',
                      borderLeft: '4px solid #14b8a6',
                      color: '#115e59',
                    }}
                  >
                    <div className="mb-1.5 flex items-center gap-2 font-sans">
                      <span
                        className="rounded px-1.5 py-0.5 text-[10px] font-semibold tracking-wide text-white"
                        style={{ background: '#0d9488' }}
                      >
                        系统注入
                      </span>
                      <span className="text-[11px] font-medium text-[#0f766e]">{segment.title}</span>
                    </div>
                    <div className="whitespace-pre-wrap font-mono">{segment.text}</div>
                  </div>
                )
              })}
              {!segments.length && (
                <span className="text-[#9ca3af]">（空提示词）</span>
              )}
            </>
          )}
        </div>
        {pathHint && !isBriefPreview && (
          <p className="shrink-0 text-[12px] text-[#9ca3af]">{pathHint}</p>
        )}
      </div>

      {!previewOnly && (
        <div className={cn('space-y-2', !editingStatic && 'hidden')}>
          <Label className="text-[15px] text-[#6b7280]">品类约束（可选）</Label>
          <Textarea
            className="min-h-[8rem] resize-y rounded-xl border-[#e5e7eb] bg-white font-mono text-[13px] leading-relaxed"
            value={value}
            onChange={e => onChange(e.target.value)}
            disabled={disabled}
            placeholder={
              isReportParameters
                ? '写品类定位习惯、易混淆项等；不要再罗列完整字段表（字段由左侧 schema 生成）。'
                : isQueryPlanner
                  ? '只写短品类约束（如表型区分、易混线索）；不要粘贴完整 Query Planner 或输出 JSON。'
                  : isAuditJudge
                    ? '只写短品类判定提醒；不要粘贴完整 Judge 提示词或判定约定正文（约定在左侧编辑）。'
                    : '写本步可选品类约束；任务场景与本步任务由系统框架自动前置。本步结构化规则请在左侧「本步补充规则」编辑。'
            }
          />
        </div>
      )}
    </div>
  )
}

