import { Suspense, type ReactNode, lazy } from 'react'
import { createBrowserRouter, Navigate, useParams } from 'react-router-dom'
import { RootLayout } from '@/layouts/root-layout'
import { KnowledgeLayout } from '@/layouts/kb-layout'
import DatasetWorkflowPage from '@/pages/dataset/workflow'
import SettingsPage from '@/pages/settings'
import FieldsSettingsPage from '@/pages/settings/fields'
import AssistantTemplateSettingsPage from '@/pages/settings/assistant-template'

const WorkbenchPage = lazy(() => import('@/pages/workbench'))
const DatasetsPage = lazy(() => import('@/pages/datasets'))
const DatasetFilesPage = lazy(() => import('@/pages/dataset/files'))
const DatasetChunksPage = lazy(() => import('@/pages/dataset/chunks'))
const DatasetRetrievalPage = lazy(() => import('@/pages/dataset/retrieval'))
const DatasetMetadataPage = lazy(() => import('@/pages/dataset/metadata'))
const DatasetSettingsPage = lazy(() => import('@/pages/dataset/settings'))
const ChunkPage = lazy(() => import('@/pages/chunk'))
const AssistantsPage = lazy(() => import('@/pages/assistants'))
const BusinessAnalyticsPage = lazy(() => import('@/pages/analytics'))
function RouteFallback() {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 backdrop-blur-[1px]">
      <div className="size-8 animate-spin rounded-full border-2 border-accent-primary border-t-transparent" />
    </div>
  )
}

function withSuspense(element: ReactNode) {
  return <Suspense fallback={<RouteFallback />}>{element}</Suspense>
}

function LegacyDatasetChatRedirect() {
  const { id = '' } = useParams()
  return <Navigate to={`/analytics?knowledge_base_id=${encodeURIComponent(id)}`} replace />
}

export const router = createBrowserRouter([
  {
    path: '/',
    element: <RootLayout />,
    children: [
      { index: true, element: withSuspense(<WorkbenchPage />) },
      { path: 'knowledge-bases', element: withSuspense(<DatasetsPage />) },
      { path: 'analytics', element: withSuspense(<BusinessAnalyticsPage />) },
      // Keep old home path working for bookmarks.
      { path: 'datasets', element: <Navigate to="/knowledge-bases" replace /> },
      {
        path: 'kb/:id',
        element: <KnowledgeLayout />,
        children: [
          { index: true, element: <Navigate to="files" replace /> },
          { path: 'files', element: withSuspense(<DatasetFilesPage />) },
          { path: 'retrieval', element: withSuspense(<DatasetRetrievalPage />) },
          { path: 'overview', element: <Navigate to="../files" replace /> },
          { path: 'settings', element: withSuspense(<DatasetSettingsPage />) },
          { path: 'rules', element: <Navigate to="../settings" replace /> },
          // The unified Agent chat now owns this entry point. Keep old bookmarks working.
          { path: 'chat', element: <LegacyDatasetChatRedirect /> },
          { path: 'workflow', element: withSuspense(<DatasetWorkflowPage />) },
          // Not in the sidebar; kept routeable for deep links and file-row overflow actions.
          { path: 'chunks', element: withSuspense(<DatasetChunksPage />) },
          { path: 'metadata', element: withSuspense(<DatasetMetadataPage />) },
        ],
      },
      // Legacy assistant routes redirect into knowledge-base / settings.
      { path: 'assistants', element: withSuspense(<AssistantsPage />) },
      { path: 'assistants/:id', element: withSuspense(<AssistantsPage />) },
      // Legacy bookmark: workflow tracing now lives inside each audit history record.
      { path: 'runs', element: <Navigate to="/" replace /> },
      { path: 'settings', element: <SettingsPage /> },
      { path: 'settings/fields', element: <FieldsSettingsPage /> },
      { path: 'settings/assistant-template', element: <AssistantTemplateSettingsPage /> },
    ],
  },
  {
    path: '/chunk/:docId',
    element: withSuspense(<ChunkPage />),
  },
  {
    path: '*',
    element: <Navigate to="/" replace />,
  },
])
