import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from 'react-router-dom'
import { Toaster } from 'sonner'
import { HelpModeProvider } from '@/components/help-mode'
import { ThemeProvider } from '@/components/theme-provider'
import { TooltipProvider } from '@/components/ui/tooltip'
import { router } from '@/routes'
import '@/styles/tokens.css'
import './App.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
})

export default function App() {
  return (
    <ThemeProvider defaultTheme="light">
      <HelpModeProvider>
        <QueryClientProvider client={queryClient}>
          <TooltipProvider delayDuration={120}>
            <RouterProvider router={router} />
            <Toaster position="top-right" richColors closeButton />
          </TooltipProvider>
        </QueryClientProvider>
      </HelpModeProvider>
    </ThemeProvider>
  )
}
