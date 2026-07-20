import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { FolderOpen, Plus } from 'lucide-react'
import { toast } from 'sonner'
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
        description="按业务类别组织文件、切片与检索范围"
        search={query}
        onSearchChange={setQuery}
        searchPlaceholder="搜索知识库"
        rightPanel={
          <Button onClick={() => setOpen(true)}>
            <Plus />
            新建知识库
          </Button>
        }
      />

      <div className="mt-6">
        {isLoading ? (
          <div className="py-16 text-center text-sm text-text-secondary">加载中…</div>
        ) : filtered.length ? (
          <CardContainer>
            {filtered.map(item => (
              <HomeCard
                key={item.id}
                title={item.name}
                description={item.description || '暂无说明'}
                onClick={() => navigate(`/kb/${item.id}/files`)}
                meta={
                  <>
                    <span>{item.file_count} 个文件</span>
                    <span>{item.chunk_count} 个切片</span>
                    <span>{formatDate(item.updated_at)}</span>
                    {item.is_default && <span className="text-accent-primary">默认</span>}
                  </>
                }
              />
            ))}
          </CardContainer>
        ) : (
          <EmptyState
            icon={<FolderOpen />}
            title="没有匹配的知识库"
            description="修改搜索词或新建一个知识库。"
            actionLabel="新建知识库"
            onAction={() => setOpen(true)}
          />
        )}
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>新建知识库</DialogTitle>
            <DialogDescription>知识库定义文件与检索范围，不承载助手提示词。</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="kb-name">名称</Label>
              <Input id="kb-name" autoFocus value={name} onChange={e => setName(e.target.value)} placeholder="例如：电缆标准" />
            </div>
            <div className="space-y-2">
              <Label htmlFor="kb-desc">说明</Label>
              <Textarea id="kb-desc" value={description} onChange={e => setDescription(e.target.value)} placeholder="描述该知识库收录的业务资料" />
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
