import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

const STORAGE_KEY = 'chunkstudio.helpMode'

type HelpModeContextValue = {
  enabled: boolean
  setEnabled: (value: boolean) => void
  toggle: () => void
}

const HelpModeContext = createContext<HelpModeContextValue | null>(null)

export function HelpModeProvider({ children }: { children: ReactNode }) {
  const [enabled, setEnabledState] = useState(() => localStorage.getItem(STORAGE_KEY) === '1')

  useEffect(() => {
    document.documentElement.classList.toggle('help-mode', enabled)
  }, [enabled])

  const setEnabled = useCallback((value: boolean) => {
    setEnabledState(value)
    localStorage.setItem(STORAGE_KEY, value ? '1' : '0')
  }, [])

  const toggle = useCallback(() => setEnabled(!enabled), [enabled, setEnabled])

  const value = useMemo(() => ({ enabled, setEnabled, toggle }), [enabled, setEnabled, toggle])

  return <HelpModeContext.Provider value={value}>{children}</HelpModeContext.Provider>
}

export function useHelpMode() {
  const context = useContext(HelpModeContext)
  if (!context) throw new Error('useHelpMode must be used within HelpModeProvider')
  return context
}
