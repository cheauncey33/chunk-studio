import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { FolderOpen, Pencil, Plus, Settings, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { api, type KnowledgeBase } from '@/api'
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
import { queryKeys, useCreateKnowledgeBase, useKnowledgeBases } from '@/hooks/use-knowledge-request'
import { helpText } from '@/lib/help-text'
import { formatDate } from '@/lib/utils'

export default function DatasetsPage() {
  const navigate = useNavigate()
  const client = useQueryClient()
  const { data: knowledgeBases = [], isLoading } = useKnowledgeBases()
  const createKb = useCreateKnowledgeBase()
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [renameTarget, setRenameTarget] = useState<KnowledgeBase | null>(null)
  const [renameName, setRenameName] = useState('')
  const [renameDescription, setRenameDescription] = useState('')
  const [renaming, setRenaming] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<KnowledgeBase | null>(null)
  const [deleting, setDeleting] = useState(false)

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

  const openRename = (item: KnowledgeBase) => {
    setRenameTarget(item)
    setRenameName(item.name)
    setRenameDescription(item.description || '')
  }

  const saveRename = async () => {
    if (!renameTarget || !renameName.trim()) return
    setRenaming(true)
    try {
      await api.updateKnowledgeBase(renameTarget.id, {
        name: renameName.trim(),
        description: renameDescription.trim(),
      })
      await client.invalidateQueries({ queryKey: queryKeys.knowledgeBases })
      toast.success('已更新知识库')
      setRenameTarget(null)
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setRenaming(false)
    }
  }

  const confirmDelete = async () => {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      const result = await api.deleteKnowledgeBase(deleteTarget.id)
      await client.invalidateQueries({ queryKey: queryKeys.knowledgeBases })
      toast.success(
        `已删除「${deleteTarget.name}」（${result.deleted_file_count} 个文件，${result.deleted_chunk_count} 段内容）`,
      )
      setDeleteTarget(null)
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="h-full overflow-auto px-8 py-7">
      <div className="mx-auto w-full max-w-7xl">
      <ListFilterBar
        title="知识库"
        titleHelp={helpText.datasets.page}
        description="把同一类 PDF 资料放在一起，方便上传、查找和审查。"
        search={query}
        onSearchChange={setQuery}
        searchPlaceholder="搜索知识库"
        searchHelp={helpText.datasets.search}
        rightPanel={
          <Button onClick={() => setOpen(true)} title={helpText.datasets.create}>
            <Plus />
            新建知识库
          </Button>
        }
      />

      <div className="mt-8">
        {isLoading ? (
          <div className="py-16 text-center text-[15px] text-text-secondary">加载中…</div>
        ) : filtered.length ? (
          <CardContainer>
            {filtered.map(item => (
              <HomeCard
                key={item.id}
                title={item.name}
                description={item.description || '暂无说明'}
                onClick={() => navigate(`/kb/${item.id}/files`)}
                titleAction={
                  <Button
                    type="button"
                    size="icon"
                    variant="ghost"
                    className="size-7"
                    title="重命名"
                    onClick={() => openRename(item)}
                  >
                    <Pencil className="size-3.5" />
                  </Button>
                }
                actions={
                  <>
                    {!item.is_default && (
                      <Button
                        type="button"
                        size="icon"
                        variant="ghost"
                        className="size-8 text-text-secondary hover:text-state-error"
                        title="删除知识库"
                        onClick={() => setDeleteTarget(item)}
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    )}
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      className="size-8"
                      title="配置"
                      onClick={() => navigate(`/kb/${item.id}/settings`)}
                    >
                      <Settings className="size-3.5" />
                    </Button>
                  </>
                }
                meta={
                  <>
                    <span>{item.file_count} 个文件</span>
                    <span>{item.chunk_count} 段内容</span>
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
            description="换个搜索词，或新建一个知识库开始。"
            actionLabel="新建知识库"
            onAction={() => setOpen(true)}
          />
        )}
      </div>
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>新建知识库</DialogTitle>
            <DialogDescription>先建库，再上传 PDF。提示词请到「审查助手」里配置。</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="kb-name" title={helpText.datasets.createName}>名称</Label>
              <Input id="kb-name" autoFocus value={name} onChange={e => setName(e.target.value)} placeholder="例如：油浸式变压器标准" />
            </div>
            <div className="space-y-2">
              <Label htmlFor="kb-desc" title={helpText.datasets.createDesc}>说明</Label>
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

      <Dialog open={Boolean(renameTarget)} onOpenChange={openDialog => !openDialog && setRenameTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>重命名知识库</DialogTitle>
            <DialogDescription>修改名称和说明。切片规则等请到库内「配置」页。</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="kb-rename-name">名称</Label>
              <Input
                id="kb-rename-name"
                autoFocus
                value={renameName}
                onChange={e => setRenameName(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="kb-rename-desc">说明</Label>
              <Textarea
                id="kb-rename-desc"
                value={renameDescription}
                onChange={e => setRenameDescription(e.target.value)}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRenameTarget(null)}>取消</Button>
            <Button disabled={!renameName.trim() || renaming} onClick={saveRename}>
              {renaming ? '保存中…' : '保存'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(deleteTarget)} onOpenChange={openDialog => !openDialog && setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除知识库</DialogTitle>
            <DialogDescription>
              {deleteTarget
                ? `确认删除「${deleteTarget.name}」？将删除其中 ${deleteTarget.file_count} 个文件、共 ${deleteTarget.chunk_count} 段内容。此操作无法撤销。`
                : null}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>取消</Button>
            <Button variant="destructive" disabled={deleting} onClick={confirmDelete}>
              {deleting ? '删除中…' : '确认删除'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
