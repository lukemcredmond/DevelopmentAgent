import { useCallback, useEffect, useState } from 'react'

const STORAGE_KEY = 'openhands-theme'

export type Theme = 'dark' | 'light'

function applyTheme(theme: Theme) {
  const root = document.documentElement
  if (theme === 'dark') {
    root.classList.add('dark')
  } else {
    root.classList.remove('dark')
  }
}

function readStoredTheme(): Theme {
  const stored = localStorage.getItem(STORAGE_KEY)
  if (stored === 'light' || stored === 'dark') return stored
  return 'dark'
}

export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(() => {
    const initial = readStoredTheme()
    applyTheme(initial)
    return initial
  })

  const setTheme = useCallback((next: Theme) => {
    localStorage.setItem(STORAGE_KEY, next)
    applyTheme(next)
    setThemeState(next)
  }, [])

  const toggleTheme = useCallback(() => {
    setTheme(theme === 'dark' ? 'light' : 'dark')
  }, [theme, setTheme])

  useEffect(() => {
    applyTheme(theme)
  }, [theme])

  return { theme, setTheme, toggleTheme, isDark: theme === 'dark' }
}
