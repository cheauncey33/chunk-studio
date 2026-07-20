import { useNavigate, useParams } from 'react-router-dom'
import { MetadataSuggestionsPage } from '@/MetadataSuggestionsPage'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export default function DatasetMetadataPage() {
  const { id } = useParams()
  const navigate = useNavigate()

  return (
    <Card className="min-h-full border-0 bg-transparent shadow-none">
      <CardHeader className="px-0 pt-2">
        <CardTitle>元数据审核</CardTitle>
        <CardDescription>
          审核 LLM 生成的切片元数据建议。知识库范围：{id}
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
