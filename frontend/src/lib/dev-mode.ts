import { useEffect, useState } from 'react'

export const DEV_MODE_STORAGE_KEY = 'chunk-studio:assistant-dev-mode'
export const DEV_MODE_EVENT = 'chunk-studio:dev-mode'

export function readDevMode(): boolean {
  try {
    return localStorage.getItem(DEV_MODE_STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

export function writeDevMode(enabled: boolean): void {
  try {
    localStorage.setItem(DEV_MODE_STORAGE_KEY, enabled ? '1' : '0')
  } catch {
    /* ignore quota / private mode */
  }
  window.dispatchEvent(new Event(DEV_MODE_EVENT))
}

/** 系统设置里的开发者模式：打开后审查配置可编辑各步 LLM 提示词。 */
export function useDevMode(): [boolean, (enabled: boolean) => void] {
  const [enabled, setEnabled] = useState(readDevMode)

  useEffect(() => {
    const sync = () => setEnabled(readDevMode())
    window.addEventListener('storage', sync)
    window.addEventListener(DEV_MODE_EVENT, sync)
    return () => {
      window.removeEventListener('storage', sync)
      window.removeEventListener(DEV_MODE_EVENT, sync)
    }
  }, [])

  return [
    enabled,
    (next: boolean) => {
      writeDevMode(next)
      setEnabled(next)
    },
  ]
}
