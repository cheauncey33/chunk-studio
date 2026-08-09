import { useEffect, useMemo, useState } from 'react'
import { BarChart3, Database, LoaderCircle, PieChart, Send, ShieldCheck } from 'lucide-react'
import { api, type BusinessChart, type BusinessOverview, type BusinessQueryResult } from '@/api'
import { Button } from '@/components/ui/button'

const EXAMPLES = [
  '现在有多少个知识库？',
  '有多少条审查记录？',
  '画出审查状态分布饼图',
  '按检测项目统计不符合项数量',
]
const STATUS_LABELS: Record<string, string> = {
  supported: '符合',
  mismatch: '不符合',
  insufficient_context: '参数不足',
  not_audited: '未完成审查',
  other: '其他/旧状态',
}
const COLORS = ['#13c2c2', '#ef4444', '#f59e0b', '#94a3b8', '#6366f1', '#22c55e']

function Pie({ data, denominator }: { data: Array<{ label: string; value: number }>; denominator: number }) {
  const background = useMemo(() => {
    if (!denominator) return '#e5e7eb'
    let cursor = 0
    return `conic-gradient(${data.map((item, index) => {
      const start = cursor
      cursor += (item.value / denominator) * 100
      return `${COLORS[index % COLORS.length]} ${start}% ${cursor}%`
    }).join(',')})`
  }, [data, denominator])
  return (
    <div className="flex flex-wrap items-center gap-6">
      <div className="relative size-44 shrink-0 rounded-full" style={{ background }}>
        <div className="absolute inset-8 grid place-items-center rounded-full bg-white text-center dark:bg-bg-card">
          <strong className="text-2xl">{denominator}</strong>
          <span className="text-xs text-text-secondary">总计</span>
        </div>
      </div>
      <div className="grid gap-2">
        {data.map((item, index) => (
          <div className="flex items-center gap-2 text-sm" key={item.label}>
            <span className="size-2.5 rounded-full" style={{ backgroundColor: COLORS[index % COLORS.length] }} />
            <span className="min-w-24 text-text-secondary">{STATUS_LABELS[item.label] || item.label}</span>
            <strong>{item.value}</strong>
            <span className="text-xs text-text-secondary">
              {denominator ? `${((item.value / denominator) * 100).toFixed(1)}%` : '0%'}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

function Chart({ chart }: { chart: BusinessChart }) {
  if (chart.type === 'metric') {
    return <div className="text-5xl font-bold tracking-tight text-[#0f766e]">{String(chart.value ?? '—')}</div>
  }
  if (chart.type === 'pie') return <Pie data={chart.data} denominator={chart.denominator} />
  const maximum = Math.max(1, ...chart.data.map(item => item.value))
  return (
    <div className="grid gap-3">
      {chart.data.map((item, index) => (
        <div className="grid grid-cols-[minmax(7rem,12rem)_1fr_3rem] items-center gap-3" key={item.label}>
          <span className="truncate text-sm text-text-secondary">{STATUS_LABELS[item.label] || item.label}</span>
          <div className="h-3 overflow-hidden rounded-full bg-[#e5e7eb] dark:bg-border-button">
            <div className="h-full rounded-full" style={{ width: `${(item.value / maximum) * 100}%`, backgroundColor: COLORS[index % COLORS.length] }} />
          </div>
          <strong className="text-right text-sm">{item.value}</strong>
        </div>
      ))}
    </div>
  )
}

function ResultTable({ result }: { result: BusinessQueryResult }) {
  if (!result.rows.length) return <p className="text-sm text-text-secondary">没有符合条件的数据。</p>
  return (
    <div className="overflow-auto rounded-xl border border-border-button">
      <table className="w-full min-w-[34rem] text-left text-sm">
        <thead className="bg-[#f8fafc] dark:bg-bg-base">
          <tr>{result.columns.map(column => <th className="px-3 py-2 font-semibold" key={column}>{column}</th>)}</tr>
        </thead>
        <tbody>
          {result.rows.map((row, index) => (
            <tr className="border-t border-border-button" key={index}>
              {result.columns.map(column => <td className="px-3 py-2 text-text-secondary" key={column}>{String(row[column] ?? '—')}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function BusinessAnalyticsPage() {
  const [overview, setOverview] = useState<BusinessOverview | null>(null)
  const [question, setQuestion] = useState('')
  const [result, setResult] = useState<BusinessQueryResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    api.getBusinessOverview()
      .then(value => { if (active) setOverview(value) })
      .catch(err => { if (active) setError(err instanceof Error ? err.message : String(err)) })
    return () => { active = false }
  }, [])

  async function submit(nextQuestion = question) {
    const normalized = nextQuestion.trim()
    if (!normalized || loading) return
    setQuestion(normalized)
    setLoading(true)
    setError('')
    try {
      setResult(await api.queryBusinessData(normalized))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  const metrics = overview?.metrics
  const metricCards = [
    ['知识库', metrics?.knowledge_bases, Database],
    ['文件', metrics?.files, Database],
    ['已批准切片', metrics?.approved_chunks, BarChart3],
    ['审查报告', metrics?.audit_reports, BarChart3],
    ['审查记录', metrics?.audit_cases, PieChart],
  ] as const
  return (
    <main className="h-full overflow-y-auto bg-[#f7f8fa] px-5 py-6 dark:bg-bg-canvas sm:px-8">
      <div className="mx-auto grid max-w-7xl gap-6">
        <header className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="mb-1 text-sm font-semibold text-[#0f766e]">只读业务数据层</p>
            <h1 className="text-3xl font-bold tracking-tight">业务问答与审查聚合</h1>
            <p className="mt-2 text-sm text-text-secondary">固定统计零模型调用；复杂问题使用一次受控 Text2SQL。</p>
          </div>
          <div className="flex items-center gap-2 rounded-full border border-[#99f6e4] bg-[#f0fdfa] px-3 py-1.5 text-xs font-semibold text-[#0f766e]">
            <ShieldCheck className="size-4" /> SELECT-only · 临时数据快照
          </div>
        </header>

        <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          {metricCards.map(([label, value, Icon]) => (
            <article className="rounded-2xl border border-border-button bg-white p-4 shadow-sm dark:bg-bg-card" key={label}>
              <div className="flex items-center justify-between text-sm text-text-secondary"><span>{label}</span><Icon className="size-4" /></div>
              <strong className="mt-3 block text-3xl">{value == null ? '—' : String(value)}</strong>
            </article>
          ))}
        </section>

        <section className="grid gap-5 lg:grid-cols-[minmax(0,1.15fr)_minmax(20rem,.85fr)]">
          <article className="rounded-2xl border border-border-button bg-white p-5 shadow-sm dark:bg-bg-card">
            <h2 className="text-lg font-bold">自然语言业务查询</h2>
            <p className="mt-1 text-sm text-text-secondary">查询审查历史和系统统计，不读取标准正文。</p>
            <div className="mt-4 flex gap-2">
              <input
                className="min-w-0 flex-1 rounded-xl border border-border-button bg-transparent px-4 py-3 outline-none focus:border-[#13c2c2]"
                value={question}
                onChange={event => setQuestion(event.target.value)}
                onKeyDown={event => { if (event.key === 'Enter') void submit() }}
                placeholder="例如：按检测项目统计不符合项数量"
              />
              <Button className="h-auto rounded-xl px-4" onClick={() => void submit()} disabled={loading || !question.trim()}>
                {loading ? <LoaderCircle className="size-4 animate-spin" /> : <Send className="size-4" />} 查询
              </Button>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              {EXAMPLES.map(example => (
                <button type="button" className="rounded-full bg-[#f1f5f9] px-3 py-1.5 text-xs text-[#475569] hover:bg-[#e2e8f0] dark:bg-bg-base dark:text-text-secondary" key={example} onClick={() => void submit(example)}>
                  {example}
                </button>
              ))}
            </div>
            {error ? <p className="mt-4 rounded-xl bg-red-50 p-3 text-sm text-red-700">{error}</p> : null}
            {result ? (
              <div className="mt-6 grid gap-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 className="font-bold">{result.answer}</h3>
                  <span className="rounded-full bg-[#ecfeff] px-2.5 py-1 text-xs font-semibold text-[#0e7490]">
                    {result.route === 'fixed_metric' ? '固定工具 · 0 次模型调用' : `Text2SQL · ${result.model}`}
                  </span>
                </div>
                {result.chart ? <Chart chart={result.chart} /> : null}
                <ResultTable result={result} />
                <details className="rounded-xl bg-[#f8fafc] p-3 text-xs dark:bg-bg-base">
                  <summary className="cursor-pointer font-semibold">查看已执行 SQL</summary>
                  <pre className="mt-2 overflow-auto whitespace-pre-wrap text-text-secondary">{result.sql}</pre>
                </details>
              </div>
            ) : null}
          </article>

          <article className="rounded-2xl border border-border-button bg-white p-5 shadow-sm dark:bg-bg-card">
            <h2 className="text-lg font-bold">审查状态分布</h2>
            <p className="mb-5 mt-1 text-sm text-text-secondary">分母为当前分析快照中的全部审查 case。</p>
            {overview ? <Pie data={overview.status_distribution} denominator={overview.denominator} /> : (
              <div className="grid h-48 place-items-center"><LoaderCircle className="size-6 animate-spin text-[#13c2c2]" /></div>
            )}
          </article>
        </section>
      </div>
    </main>
  )
}
