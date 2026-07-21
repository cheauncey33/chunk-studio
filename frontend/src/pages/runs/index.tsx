import { AuditPage } from '@/AuditPage'
import { Explain } from '@/components/explain'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { helpText } from '@/lib/help-text'

export default function RunsPage() {
  return (
    <div className="h-full overflow-auto p-5">
      <Card className="border-0 bg-transparent shadow-none">
        <CardHeader className="flex-row items-end justify-between space-y-0 px-0">
          <div>
            <Explain text={helpText.runs.page} title="运行记录">
              <CardTitle>运行记录</CardTitle>
            </Explain>
            <CardDescription>
              回看每次审查跑完的过程：每一步做了什么、输入输出是什么。评测明细默认收起。
            </CardDescription>
          </div>
          <Explain text={helpText.runs.assistantFilter} title="按助手筛选">
            <Tooltip>
              <TooltipTrigger asChild>
                <span>
                  <select
                    disabled
                    className="h-10 w-56 cursor-not-allowed rounded-md border border-border-button bg-bg-input px-3 text-sm text-text-secondary opacity-70"
                    defaultValue="all"
                  >
                    <option value="all">全部助手</option>
                  </select>
                </span>
              </TooltipTrigger>
              <TooltipContent>按助手筛选暂未接入，请先用下方报告列表浏览。</TooltipContent>
            </Tooltip>
          </Explain>
        </CardHeader>
        <CardContent className="space-y-4 px-0">
          <div className="rounded-xl border border-border-button bg-bg-accent px-4 py-3 text-sm">
            <strong className="text-text-primary">怎么看这些记录</strong>
            <span className="ml-2 text-text-secondary">
              点上方流程步骤，下方会显示该步的配置、提示词、输入和输出。需要核对对错时，再展开评测明细。
            </span>
          </div>
          <div className="run-history-embed overflow-hidden rounded-xl border border-border-button bg-bg-base">
            <AuditPage embedded />
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
