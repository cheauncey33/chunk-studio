import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { toast } from 'sonner'
import { Explain } from '@/components/explain'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input, Label, Textarea } from '@/components/ui/input'
import { useKnowledgeBase, useUpdateKnowledgeBase } from '@/hooks/use-knowledge-request'
import { helpText } from '@/lib/help-text'

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
        <Explain text={helpText.kbNav.settings} title="库设置">
          <CardTitle>库设置</CardTitle>
        </Explain>
        <CardDescription>
          只改这个知识库的名称和说明。提示词、检索松紧请到「审查助手」里改。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Explain text={helpText.datasets.createName} title="名称">
            <Label>名称</Label>
          </Explain>
          <Input value={name} onChange={e => setName(e.target.value)} />
        </div>
        <div className="space-y-2">
          <Explain text={helpText.datasets.createDesc} title="说明">
            <Label>说明</Label>
          </Explain>
          <Textarea value={description} onChange={e => setDescription(e.target.value)} />
        </div>
        <Button disabled={updateKb.isPending} onClick={save}>
          {updateKb.isPending ? '保存中…' : '保存设置'}
        </Button>
      </CardContent>
    </Card>
  )
}
