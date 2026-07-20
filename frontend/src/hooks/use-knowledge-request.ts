import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api'

export const queryKeys = {
  knowledgeBases: ['knowledgeBases'] as const,
  knowledgeBase: (id: string) => ['knowledgeBases', id] as const,
  kbFiles: (id: string) => ['knowledgeBases', id, 'files'] as const,
  kbChunks: (id: string) => ['knowledgeBases', id, 'chunks'] as const,
  assistants: ['assistants'] as const,
  assistantVersion: (id: string) => ['assistants', id, 'version'] as const,
  assistantVersions: (id: string) => ['assistants', id, 'versions'] as const,
  files: ['files'] as const,
  chunks: (fileId: string) => ['chunks', fileId] as const,
  fields: ['fields'] as const,
  settings: ['settings'] as const,
}

export function useKnowledgeBases() {
  return useQuery({
    queryKey: queryKeys.knowledgeBases,
    queryFn: () => api.listKnowledgeBases(),
  })
}

export function useKnowledgeBase(id: string | undefined) {
  const list = useKnowledgeBases()
  return {
    ...list,
    data: id ? list.data?.find(item => item.id === id) : undefined,
  }
}

export function useKbFiles(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.kbFiles(id || ''),
    queryFn: () => api.listKnowledgeBaseFiles(id!),
    enabled: Boolean(id),
  })
}

export function useKbChunks(id: string | undefined, limit = 100) {
  return useQuery({
    queryKey: [...queryKeys.kbChunks(id || ''), limit],
    queryFn: () => api.listKnowledgeBaseChunks(id!, limit),
    enabled: Boolean(id),
  })
}

export function useAssistants() {
  return useQuery({
    queryKey: queryKeys.assistants,
    queryFn: () => api.listAssistants(),
  })
}

export function useFields() {
  return useQuery({
    queryKey: queryKeys.fields,
    queryFn: () => api.listFields(),
  })
}

export function useFileChunks(fileId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.chunks(fileId || ''),
    queryFn: () => api.listChunks(fileId),
    enabled: Boolean(fileId),
  })
}

export function useCreateKnowledgeBase() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: { name: string; description: string }) => api.createKnowledgeBase(body),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.knowledgeBases }),
  })
}

export function useUpdateKnowledgeBase(id: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: { name?: string; description?: string }) => api.updateKnowledgeBase(id, body),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.knowledgeBases }),
  })
}
