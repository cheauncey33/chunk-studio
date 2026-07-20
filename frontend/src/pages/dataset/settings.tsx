import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input, Label, Textarea } from '@/components/ui/input'
import { useKnowledgeBase, useUpdateKnowledgeBase } from '@/hooks/use-knowledge-request'

export default function DatasetSettingsPage() {
  const { id = '' } = useParams()
  const { data: knowledgeBase } = useKnowledgeBase(id)
  const updateKb = useUpdateKnowledgeBase(id)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')

  useEffect(() => {
    if (!knowledgeBase) return
    setName(knowledgeBase.name)
    setDescription(knowledgeBase.description)
  }, [knowledgeBase])

  const save = async () => {
    try {
      await updateKb.mutateAsync({ name, description })
      toast.success('知识库设置已保存')
    } catch (err) {
      toast.error((err as Error).message)
    }
  }

  return (
    <Card className="mt-2 max-w-2xl border-border-button bg-bg-base">
      <CardHeader>
        <CardTitle>知识库设置</CardTitle>
        <CardDescription>这里只定义资料范围；提示词和业务规则在审查助手中维护。</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Label>名称</Label>
          <Input value={name} onChange={e => setName(e.target.value)} />
        </div>
        <div className="space-y-2">
          <Label>说明</Label>
          <Textarea value={description} onChange={e => setDescription(e.target.value)} />
        </div>
        <Button disabled={updateKb.isPending} onClick={save}>
          {updateKb.isPending ? '保存中…' : '保存设置'}
        </Button>
      </CardContent>
    </Card>
  )
}
