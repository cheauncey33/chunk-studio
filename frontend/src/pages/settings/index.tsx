import { Link } from 'react-router-dom'
import { SettingsPage as LegacySettingsPage } from '@/SettingsPage'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export default function SettingsWorkspacePage() {
  return (
    <div className="h-full overflow-auto p-5">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">设置</h1>
          <p className="mt-1 text-sm text-text-secondary">管理 DeepSeek、OCR、嵌入与本地运行参数</p>
        </div>
        <Button asChild variant="outline">
          <Link to="/settings/fields">字段配置</Link>
        </Button>
      </div>
      <Card className="border-border-button bg-bg-base">
        <CardHeader>
          <CardTitle className="text-base">运行参数</CardTitle>
          <CardDescription>字段 schema 请前往「字段配置」。</CardDescription>
        </CardHeader>
        <CardContent>
          <LegacySettingsPage />
        </CardContent>
      </Card>
    </div>
  )
}
