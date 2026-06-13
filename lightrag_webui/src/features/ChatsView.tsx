import { useCallback, useEffect, useState } from 'react'
import { adminGetChat, adminListChats, adminChatsStats, AdminChatStats, ChatSummary, ServerChat } from '@/api/lightrag'
import { Card, CardContent } from '@/components/ui/Card'
import Button from '@/components/ui/Button'
import Checkbox from '@/components/ui/Checkbox'
import { ChevronDown, ChevronRight, ChevronLeft, FileText, Clock, Hash, ThumbsUp, ThumbsDown, RefreshCw, Trash2 } from 'lucide-react'
import { cn } from '@/lib/utils'

const PAGE_SIZE = 50
const nf = (n: number) => n.toLocaleString('ru-RU')
const fmtDate = (ts?: number) => (ts ? new Date(ts * 1000).toLocaleString('ru-RU') : '—')

const REASON_LABELS: Record<string, string> = {
  off_topic: 'не по теме',
  incomplete: 'неполный',
  excessive: 'лишнее',
  other: 'другое'
}

type RatingFilter = 'all' | 'positive' | 'negative' | 'none'

const RATING_FILTERS: { value: RatingFilter; label: string }[] = [
  { value: 'all', label: 'Все' },
  { value: 'positive', label: '👍 с лайком' },
  { value: 'negative', label: '👎 с дизлайком' },
  { value: 'none', label: 'Без оценки' }
]

function StatChip({ label, value, className }: { label: string; value: number; className?: string }) {
  return (
    <div className="bg-card/60 flex flex-col items-center rounded-md border px-3 py-1.5">
      <span className={cn('text-lg font-semibold leading-tight', className)}>{nf(value)}</span>
      <span className="text-muted-foreground text-xs">{label}</span>
    </div>
  )
}

export default function ChatsView() {
  const [chats, setChats] = useState<ChatSummary[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [open, setOpen] = useState<Record<string, ServerChat | null>>({})
  const [loading, setLoading] = useState(true)
  const [showArchived, setShowArchived] = useState(false)
  const [ratingFilter, setRatingFilter] = useState<RatingFilter>('all')
  const [stats, setStats] = useState<AdminChatStats | null>(null)

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  // Список — серверная пагинация + фильтры.
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    ;(async () => {
      try {
        const res = await adminListChats({ includeArchived: showArchived, page, pageSize: PAGE_SIZE, rating: ratingFilter })
        if (!cancelled) { setChats(res.items); setTotal(res.total) }
      } catch {
        /* ignore */
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [showArchived, ratingFilter, page])

  // Статистика — агрегаты по всем диалогам (считаются на сервере).
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const s = await adminChatsStats(showArchived)
        if (!cancelled) setStats(s)
      } catch { /* ignore */ }
    })()
    return () => { cancelled = true }
  }, [showArchived])

  const setFilter = useCallback((f: RatingFilter) => { setRatingFilter(f); setPage(1) }, [])
  const setArchived = useCallback((v: boolean) => { setShowArchived(v); setPage(1) }, [])

  const toggle = async (id: string) => {
    if (open[id] !== undefined) {
      setOpen((o) => {
        const next = { ...o }
        delete next[id]
        return next
      })
      return
    }
    setOpen((o) => ({ ...o, [id]: null }))
    try {
      const chat = await adminGetChat(id)
      setOpen((o) => ({ ...o, [id]: chat }))
    } catch {
      setOpen((o) => ({ ...o, [id]: null }))
    }
  }

  return (
    <div className="flex justify-center overflow-auto p-6">
      <div className="flex w-full max-w-4xl flex-col gap-4 pb-12">
        <div>
          <h2 className="font-serif text-xl font-bold">История диалогов</h2>
          <p className="text-muted-foreground text-sm">
            Все диалоги пользователей по-диалогово. Раскройте, чтобы увидеть переписку, использованные
            документы, время ответа, токены и оценки.
          </p>
        </div>

        {/* Статистика (по всем диалогам) */}
        {stats && (
          <div className="flex flex-wrap gap-2">
            <StatChip label="диалогов" value={stats.dialogs} />
            <StatChip label="сообщений" value={stats.messages} />
            <StatChip label="👍 лайков" value={stats.positive} className="text-primary" />
            <StatChip label="👎 дизлайков" value={stats.negative} className="text-destructive" />
            <StatChip label="без оценки" value={stats.none} />
            {showArchived && <StatChip label="удалённых" value={stats.archived} className="text-muted-foreground" />}
          </div>
        )}

        {/* Фильтры */}
        <div className="flex flex-wrap items-center gap-2">
          {RATING_FILTERS.map((f) => (
            <button
              key={f.value}
              onClick={() => setFilter(f.value)}
              className={cn(
                'rounded-md border px-2.5 py-1 text-xs transition-colors',
                ratingFilter === f.value ? 'bg-primary text-primary-foreground' : 'hover:bg-accent/40'
              )}
            >
              {f.label}
            </button>
          ))}
          <label className="ml-auto flex cursor-pointer items-center gap-2 text-xs">
            <Checkbox checked={showArchived} onCheckedChange={(c) => setArchived(!!c)} />
            Показывать удалённые
          </label>
        </div>

        {loading ? (
          <div className="text-muted-foreground py-12 text-center">Загрузка истории…</div>
        ) : chats.length === 0 ? (
          <div className="text-muted-foreground py-12 text-center text-lg">
            {total === 0 ? 'История пуста. Диалоги пользователей появятся здесь.' : 'Нет диалогов под выбранный фильтр.'}
          </div>
        ) : (
          chats.map((c) => {
            const isOpen = open[c.id] !== undefined
            const chat = open[c.id]
            return (
              <Card key={c.id} className={cn('overflow-hidden', c.archived && 'opacity-70')}>
                <button
                  type="button"
                  onClick={() => toggle(c.id)}
                  className="hover:bg-accent/40 flex w-full items-center gap-2 px-4 py-3 text-left transition-colors"
                >
                  {isOpen ? <ChevronDown className="size-4 shrink-0" /> : <ChevronRight className="size-4 shrink-0" />}
                  <span className="flex flex-1 items-center gap-2 truncate">
                    <span className="truncate font-medium">{c.title || 'Диалог'}</span>
                    {c.archived && (
                      <span className="text-muted-foreground flex shrink-0 items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide">
                        <Trash2 className="size-3" />удалён
                      </span>
                    )}
                  </span>
                  <span className="text-muted-foreground flex items-center gap-3 text-xs">
                    <span>{c.login}</span>
                    <span>{fmtDate(c.updated_at || c.created_at)}</span>
                    <span>{c.message_count} сообщ.</span>
                    {!!c.feedback_positive && <span className="flex items-center gap-1 text-primary"><ThumbsUp className="size-3" />{c.feedback_positive}</span>}
                    {!!c.feedback_negative && <span className="flex items-center gap-1 text-destructive"><ThumbsDown className="size-3" />{c.feedback_negative}</span>}
                  </span>
                </button>
                {isOpen && (
                  <CardContent className="flex flex-col gap-3 border-t border-border/60 pt-4">
                    {chat === null ? (
                      <div className="text-muted-foreground text-sm">Загрузка…</div>
                    ) : (
                      (chat.messages || []).map((m) => (
                        <div key={m.mid} className={cn('rounded-md p-2', m.role === 'user' ? 'bg-primary/10' : 'bg-muted/40')}>
                          <div className="text-muted-foreground mb-1 text-xs uppercase tracking-wide">
                            {m.role === 'user' ? 'Вопрос' : 'Ответ'}
                          </div>
                          <div className="text-sm whitespace-pre-wrap break-words max-h-72 overflow-auto">{m.content || '—'}</div>
                          {m.role === 'assistant' && (
                            <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                              {m.rewritten_query && (
                                <span className="flex items-center gap-1" title={m.rewritten_query}><RefreshCw className="size-3" />реврайт</span>
                              )}
                              {m.used_docs && m.used_docs.length > 0 && (
                                <span className="flex items-center gap-1" title={m.used_docs.join('\n')}><FileText className="size-3" />{m.used_docs.length} док.</span>
                              )}
                              {typeof m.latency_ms === 'number' && (
                                <span className="flex items-center gap-1"><Clock className="size-3" />{(m.latency_ms / 1000).toFixed(1)}с</span>
                              )}
                              {typeof m.total_tokens === 'number' && (
                                <span className="flex items-center gap-1"><Hash className="size-3" />{nf(m.total_tokens)}</span>
                              )}
                              {m.feedback === 'positive' && <span className="flex items-center gap-1 text-primary"><ThumbsUp className="size-3" />хорошо</span>}
                              {m.feedback === 'negative' && (
                                <span className="flex items-center gap-1 text-destructive">
                                  <ThumbsDown className="size-3" />плохо{m.feedback_reason ? ` · ${REASON_LABELS[m.feedback_reason] || m.feedback_reason}` : ''}
                                  {m.feedback_comment ? `: ${m.feedback_comment}` : ''}
                                </span>
                              )}
                            </div>
                          )}
                        </div>
                      ))
                    )}
                  </CardContent>
                )}
              </Card>
            )
          })
        )}

        {/* Пагинация */}
        {totalPages > 1 && (
          <div className="flex items-center justify-center gap-3 text-sm">
            <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
              <ChevronLeft className="size-4" />
            </Button>
            <span className="text-muted-foreground">Стр. {page} из {totalPages} · {nf(total)} диалогов</span>
            <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => setPage((p) => Math.min(totalPages, p + 1))}>
              <ChevronRight className="size-4" />
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}
