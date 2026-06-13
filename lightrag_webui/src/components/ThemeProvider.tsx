import { createContext, useEffect } from 'react'
import { Theme, useSettingsStore } from '@/stores/settings'

type ThemeProviderProps = {
  children: React.ReactNode
}

type ThemeProviderState = {
  theme: Theme
  setTheme: (theme: Theme) => void
}

const initialState: ThemeProviderState = {
  theme: 'dark',
  setTheme: () => null
}

const ThemeProviderContext = createContext<ThemeProviderState>(initialState)

/**
 * Провайдер темы. Приложение использует единственную тёмную тему:
 * класс 'dark' всегда установлен на <html>, переключение тем отключено.
 */
export default function ThemeProvider({ children, ...props }: ThemeProviderProps) {
  const storeTheme = useSettingsStore.use.theme()
  const setStoreTheme = useSettingsStore.use.setTheme()

  useEffect(() => {
    const root = window.document.documentElement
    root.classList.remove('light')
    root.classList.add('dark')

    // Синхронизируем сохранённое значение, чтобы компоненты,
    // читающие тему напрямую из стора, тоже видели 'dark'.
    if (storeTheme !== 'dark') {
      setStoreTheme('dark')
    }
  }, [storeTheme, setStoreTheme])

  const value: ThemeProviderState = {
    theme: 'dark',
    setTheme: () => null
  }

  return (
    <ThemeProviderContext.Provider {...props} value={value}>
      {children}
    </ThemeProviderContext.Provider>
  )
}

export { ThemeProviderContext }
