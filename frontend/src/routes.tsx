import { lazy, Suspense, type ReactNode } from 'react'
import { createBrowserRouter, Navigate } from 'react-router-dom'
import { RootLayout } from '@/layouts/root-layout'
import { KnowledgeLayout } from '@/layouts/kb-layout'

const WorkbenchPage = lazy(() => import('@/pages/workbench'))
const DatasetsPage = lazy(() => import('@/pages/datasets'))
const DatasetFilesPage = lazy(() => import('@/pages/dataset/files'))
const DatasetChunksPage = lazy(() => import('@/pages/dataset/chunks'))
const DatasetRetrievalPage = lazy(() => import('@/pages/dataset/retrieval'))
const DatasetMetadataPage = lazy(() => import('@/pages/dataset/metadata'))
const DatasetSettingsPage = lazy(() => import('@/pages/dataset/settings'))
const ChunkPage = lazy(() => import('@/pages/chunk'))
const AssistantsPage = lazy(() => import('@/pages/assistants'))
const RunsPage = lazy(() => import('@/pages/runs'))
const SettingsPage = lazy(() => import('@/pages/settings'))
const FieldsSettingsPage = lazy(() => import('@/pages/settings/fields'))

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

export const router = createBrowserRouter([
  {
    path: '/',
    element: <RootLayout />,
    children: [
      { index: true, element: withSuspense(<WorkbenchPage />) },
      { path: 'knowledge-bases', element: withSuspense(<DatasetsPage />) },
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
          // Not in the sidebar; kept routeable for deep links and file-row overflow actions.
          { path: 'chunks', element: withSuspense(<DatasetChunksPage />) },
          { path: 'metadata', element: withSuspense(<DatasetMetadataPage />) },
        ],
      },
      { path: 'assistants', element: withSuspense(<AssistantsPage />) },
      { path: 'assistants/:id', element: withSuspense(<AssistantsPage />) },
      { path: 'runs', element: withSuspense(<RunsPage />) },
      { path: 'settings', element: withSuspense(<SettingsPage />) },
      { path: 'settings/fields', element: withSuspense(<FieldsSettingsPage />) },
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
