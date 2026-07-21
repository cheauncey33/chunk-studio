import { useNavigate, useParams } from 'react-router-dom'
import { MetadataSuggestionsPage } from '@/MetadataSuggestionsPage'
import { Explain } from '@/components/explain'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { helpText } from '@/lib/help-text'

export default function DatasetMetadataPage() {
  const { id } = useParams()
  const navigate = useNavigate()

  return (
    <Card className="min-h-full border-0 bg-transparent shadow-none">
      <CardHeader className="px-0 pt-2">
        <Explain text={helpText.kbNav.metadata} title="AI 建议审核">
          <CardTitle>AI 建议审核</CardTitle>
        </Explain>
        <CardDescription>
          AI 给内容片段写的关键词、问题等建议。点「采纳」后才会真正写进业务信息；点条目可跳到编辑器核对原文。
        </CardDescription>
      </CardHeader>
      <CardContent className="metadata-embed px-0">
        <MetadataSuggestionsPage
          onLocate={chunk => navigate(`/chunk/${chunk.file_id}?kb=${id}&chunk=${chunk.id}&page=${chunk.page}`)}
        />
      </CardContent>
    </Card>
  )
}
