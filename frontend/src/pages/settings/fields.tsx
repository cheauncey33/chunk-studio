import { Link } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { FieldConfigPanel } from '@/FieldConfigPanel'
import { Explain } from '@/components/explain'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { queryKeys, useFields } from '@/hooks/use-knowledge-request'
import { helpText } from '@/lib/help-text'

export default function FieldsSettingsPage() {
  const { data: fields = [], isLoading } = useFields()
  const client = useQueryClient()

  return (
    <div className="h-full overflow-auto p-5">
      <div className="mb-5 flex items-center gap-3">
        <Button asChild variant="ghost" size="sm">
          <Link to="/settings">← 返回设置</Link>
        </Button>
      </div>
      <Card className="border-border-button bg-bg-base">
        <CardHeader>
          <Explain text={helpText.settings.fields} title="字段配置">
            <CardTitle>字段配置</CardTitle>
          </Explain>
          <CardDescription>
            决定「内容编辑器」里能填哪些业务信息（例如标准号、条款号）。改的是模板，不是某一篇 PDF。
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="py-8 text-sm text-text-secondary">加载中…</div>
          ) : (
            <FieldConfigPanel
              fields={fields}
              onChanged={() => client.invalidateQueries({ queryKey: queryKeys.fields })}
            />
          )}
        </CardContent>
      </Card>
    </div>
  )
}
