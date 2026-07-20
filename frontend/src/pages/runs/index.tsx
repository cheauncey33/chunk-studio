import { useState } from 'react'
import { AuditPage } from '@/AuditPage'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useAssistants } from '@/hooks/use-knowledge-request'

export default function RunsPage() {
  const { data: assistants = [] } = useAssistants()
  const [assistantFilter, setAssistantFilter] = useState('all')

  return (
    <div className="h-full overflow-auto p-5">
      <Card className="border-0 bg-transparent shadow-none">
        <CardHeader className="flex-row items-end justify-between space-y-0 px-0">
          <div>
            <CardTitle>助手运行记录</CardTitle>
            <CardDescription>按助手和版本回看报告、流程节点、实际输入与输出</CardDescription>
          </div>
          <div className="w-56">
            <Select value={assistantFilter} onValueChange={setAssistantFilter}>
              <SelectTrigger>
                <SelectValue placeholder="审查助手" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部助手</SelectItem>
                {assistants.map(item => (
                  <SelectItem key={item.id} value={item.id}>{item.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </CardHeader>
        <CardContent className="space-y-4 px-0">
          <div className="rounded-xl border border-border-button bg-bg-accent px-4 py-3 text-sm">
            <strong className="text-text-primary">历史记录归属说明</strong>
            <span className="ml-2 text-text-secondary">
              现有油浸式端到端报告已作为“油浸式变压器审查 · v1”的历史运行展示；旧报告没有版本字段时会标记为“旧报告还原”。
            </span>
          </div>
          {assistantFilter !== 'all' && (
            <p className="text-xs text-text-secondary">
              当前筛选助手：{assistants.find(item => item.id === assistantFilter)?.name || assistantFilter}
              （报告列表仍展示全部历史；后端助手过滤接入后将生效）
            </p>
          )}
          <div className="run-history-embed overflow-hidden rounded-xl border border-border-button bg-bg-base">
            <AuditPage />
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
