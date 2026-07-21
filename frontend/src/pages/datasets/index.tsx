import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { FolderOpen, Plus } from 'lucide-react'
import { toast } from 'sonner'
import { Explain } from '@/components/explain'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input, Label, Textarea } from '@/components/ui/input'
import { ListFilterBar } from '@/components/list-filter-bar'
import { EmptyState } from '@/components/empty-state'
import { CardContainer, HomeCard } from '@/components/home-card'
import { useCreateKnowledgeBase, useKnowledgeBases } from '@/hooks/use-knowledge-request'
import { helpText } from '@/lib/help-text'
import { formatDate } from '@/lib/utils'

export default function DatasetsPage() {
  const navigate = useNavigate()
  const { data: knowledgeBases = [], isLoading } = useKnowledgeBases()
  const createKb = useCreateKnowledgeBase()
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')

  const filtered = knowledgeBases.filter(item =>
    `${item.name} ${item.description}`.toLowerCase().includes(query.trim().toLowerCase()),
  )

  const create = async () => {
    if (!name.trim()) return
    try {
      const created = await createKb.mutateAsync({ name: name.trim(), description: description.trim() })
      toast.success('知识库已创建')
      setOpen(false)
      setName('')
      setDescription('')
      navigate(`/kb/${created.id}/files`)
    } catch (err) {
      toast.error((err as Error).message)
    }
  }

  return (
    <div className="h-full overflow-auto p-5">
      <ListFilterBar
        title="知识库"
        titleHelp={helpText.datasets.page}
        description="把同一类 PDF 资料放在一起，方便上传、查找和审查。"
        search={query}
        onSearchChange={setQuery}
        searchPlaceholder="搜索知识库"
        searchHelp={helpText.datasets.search}
        rightPanel={
          <Explain text={helpText.datasets.create} title="新建知识库">
            <Button onClick={() => setOpen(true)}>
              <Plus />
              新建知识库
            </Button>
          </Explain>
        }
      />

      <div className="mt-6">
        {isLoading ? (
          <div className="py-16 text-center text-sm text-text-secondary">加载中…</div>
        ) : filtered.length ? (
          <CardContainer>
            {filtered.map(item => (
              <Explain key={item.id} text={helpText.datasets.openCard} title={item.name} className="block w-full">
                <HomeCard
                  title={item.name}
                  description={item.description || '暂无说明'}
                  onClick={() => navigate(`/kb/${item.id}/files`)}
                  meta={
                    <>
                      <span>{item.file_count} 个文件</span>
                      <span>{item.chunk_count} 段内容</span>
                      <span>{formatDate(item.updated_at)}</span>
                      {item.is_default && <span className="text-accent-primary">默认</span>}
                    </>
                  }
                />
              </Explain>
            ))}
          </CardContainer>
        ) : (
          <EmptyState
            icon={<FolderOpen />}
            title="没有匹配的知识库"
            description="换个搜索词，或新建一个知识库开始。"
            actionLabel="新建知识库"
            onAction={() => setOpen(true)}
          />
        )}
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>新建知识库</DialogTitle>
            <DialogDescription>先建库，再上传 PDF。提示词请到「审查助手」里配置。</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Explain text={helpText.datasets.createName} title="名称">
                <Label htmlFor="kb-name">名称</Label>
              </Explain>
              <Input id="kb-name" autoFocus value={name} onChange={e => setName(e.target.value)} placeholder="例如：电缆标准" />
            </div>
            <div className="space-y-2">
              <Explain text={helpText.datasets.createDesc} title="说明">
                <Label htmlFor="kb-desc">说明</Label>
              </Explain>
              <Textarea id="kb-desc" value={description} onChange={e => setDescription(e.target.value)} placeholder="这个库收哪些资料（可选）" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>取消</Button>
            <Button disabled={!name.trim() || createKb.isPending} onClick={create}>
              {createKb.isPending ? '创建中…' : '创建'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
