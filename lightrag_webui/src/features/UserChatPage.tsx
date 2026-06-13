import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { LogOutIcon, FileTextIcon, ArrowLeftIcon } from 'lucide-react'
import { useAuthStore, useBackendState } from '@/stores/state'
import Logo from '@/components/Logo'
import { SiteInfo } from '@/lib/constants'
import { listMyDocs, getMyDocContent, MyDoc } from '@/api/lightrag'
import ChatPanel from '@/features/ChatPanel'
import StatusIndicator from '@/components/status/StatusIndicator'
import Button from '@/components/ui/Button'
import { toast } from 'sonner'

function MyDocsPanel() {
  const [docs, setDocs] = useState<MyDoc[]>([])
  const [openDoc, setOpenDoc] = useState<{ file_path: string; content: string } | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    ;(async () => {
      try { setDocs(await listMyDocs()) } catch { /* ignore */ }
    })()
  }, [])

  const view = useCallback(async (fp: string) => {
    setLoading(true)
    try {
      setOpenDoc(await getMyDocContent(fp))
    } catch {
      toast.error('Не удалось открыть документ')
    } finally {
      setLoading(false)
    }
  }, [])

  return (
    <div className="flex w-72 shrink-0 flex-col overflow-hidden rounded-lg border bg-card/40">
      <div className="border-b px-3 py-2 text-sm font-medium">Мои документы</div>
      {openDoc ? (
        <div className="flex min-h-0 flex-1 flex-col">
          <button className="flex items-center gap-1 px-3 py-2 text-xs text-muted-foreground hover:text-foreground" onClick={() => setOpenDoc(null)}>
            <ArrowLeftIcon className="size-3.5" /> назад к списку
          </button>
          <div className="px-3 pb-1 text-sm font-medium break-words">{openDoc.file_path}</div>
          <div className="flex-1 overflow-auto whitespace-pre-wrap break-words px-3 pb-3 text-xs text-foreground/90">
            {openDoc.content || '—'}
          </div>
        </div>
      ) : (
        <div className="flex-1 overflow-auto p-2">
          {docs.length === 0 ? (
            <div className="text-muted-foreground p-2 text-xs">Документы вашему отделу пока не назначены.</div>
          ) : (
            docs.map((d) => (
              <button
                key={d.file_path}
                onClick={() => view(d.file_path)}
                disabled={loading}
                className="flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-accent/40"
                title={d.content_summary}
              >
                <FileTextIcon className="mt-0.5 size-3.5 shrink-0 opacity-60" />
                <span className="break-words">{d.file_path}</span>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  )
}

export default function UserChatPage() {
  const navigate = useNavigate()
  const { username, logout, appName } = useAuthStore()

  // На странице /chat нет таймера здоровья из App — запускаем свой опрос,
  // чтобы индикатор «Подключено» был актуальным и у пользователя.
  useEffect(() => {
    const tick = () => { useBackendState.getState().check().catch(() => {}) }
    tick()
    const id = setInterval(tick, 30000)
    return () => clearInterval(id)
  }, [])

  const handleLogout = () => {
    logout()
    navigate('/login')
  }

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden">
      <header className="border-border/60 bg-sidebar/95 supports-[backdrop-filter]:bg-sidebar/75 sticky top-0 z-50 flex h-12 w-full items-center border-b px-4 backdrop-blur">
        <div className="flex items-center gap-2">
          <Logo className="size-6 text-primary" />
          <span className="font-serif text-lg font-bold">{appName || SiteInfo.name}</span>
        </div>
        <StatusIndicator className="ml-3 hidden sm:flex" />
        <div className="flex-1" />
        <div className="flex items-center gap-3">
          {username && <span className="text-muted-foreground text-sm">{username}</span>}
          <Button variant="ghost" size="sm" onClick={handleLogout}>
            <LogOutIcon className="size-4" /> Выйти
          </Button>
        </div>
      </header>
      <div className="flex min-h-0 flex-1 gap-3 p-3 overflow-hidden">
        <div className="min-w-0 flex-1 overflow-hidden rounded-lg">
          <ChatPanel />
        </div>
        <MyDocsPanel />
      </div>
    </div>
  )
}
