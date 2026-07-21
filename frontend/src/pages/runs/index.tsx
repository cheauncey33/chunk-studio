import { useSearchParams } from 'react-router-dom'
import { AuditPage } from '@/AuditPage'
import { Explain } from '@/components/explain'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { helpText } from '@/lib/help-text'

export default function RunsPage() {
  const [params] = useSearchParams()
  const initialReport = params.get('report') || ''

  return (
    <div className="h-full overflow-auto p-5">
      <Card className="border-0 bg-transparent shadow-none">
        <CardHeader className="flex-row items-end justify-between space-y-0 px-0">
          <div>
            <Explain text={helpText.runs.page} title="结果详情">
              <CardTitle>结果详情</CardTitle>
            </Explain>
            <CardDescription>
              需要核对过程时再看这里。日常结论请回「审查」首页。
            </CardDescription>
          </div>
        </CardHeader>
        <CardContent className="space-y-4 px-0">
          <div className="run-history-embed overflow-hidden rounded-xl border border-border-button bg-bg-base">
            <AuditPage embedded initialReport={initialReport} />
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
