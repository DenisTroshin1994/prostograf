import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import {
  ChatSummary,
  createChat,
  deleteChat,
  getChat,
  listChats,
  rewriteQuery,
  sendChatMessageStream,
  setChatFeedback,
  ServerChatMessage
} from '@/api/lightrag'
import { useSettingsStore } from '@/stores/settings'
import { ChatMessage, MessageWithError } from '@/components/retrieval/ChatMessage'
import { parseCOTContent } from '@/utils/cot'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import { cn } from '@/lib/utils'
import {
  PlusIcon, Trash2Icon, SendIcon, MessageSquareIcon, ThumbsUpIcon, ThumbsDownIcon,
  FileTextIcon, ClockIcon, HashIcon, LoaderIcon, SquareIcon, CopyIcon, CheckIcon
} from 'lucide-react'

// Копирование в буфер обмена с запасным вариантом для не-secure контекста
// (clipboard API доступен только на https/localhost).
async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch { /* fallthrough */ }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.focus()
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  }
}

function CopyButton({ text, withLabel }: { text: string; withLabel?: boolean }) {
  const [copied, setCopied] = useState(false)
  const onCopy = async (e: React.MouseEvent) => {
    e.stopPropagation()
    if (await copyText(text)) {
      setCopied(true)
      setTimeout(() => setCopied(false), 1200)
    }
  }
  return (
    <button
      type="button"
      onClick={onCopy}
      className="flex items-center gap-1 rounded p-0.5 hover:text-primary"
      title="Копировать текст"
    >
      {copied ? <CheckIcon className="size-3" /> : <CopyIcon className="size-3" />}
      {withLabel && <span>{copied ? 'скопировано' : 'копировать'}</span>}
    </button>
  )
}

// Статусы пока ответ ещё не начал стримиться — чтобы чат не выглядел «мёртвым».
type ChatStatus = 'analyzing' | 'searching' | 'generating'

type ChatMsg = {
  mid?: string
  role: 'user' | 'assistant'
  content: string
  isError?: boolean
  usedDocs?: string[]
  totalTokens?: number
  promptTokens?: number
  completionTokens?: number
  latencyMs?: number
  rewrittenQuery?: string
  feedback?: 'positive' | 'negative'
  streaming?: boolean
  status?: ChatStatus
}

const STATUS_LABELS: Record<ChatStatus, string> = {
  analyzing: 'Анализ вопроса…',
  searching: 'Поиск по документам…',
  generating: 'Формирование ответа…'
}

const FEEDBACK_REASONS: { value: string; label: string }[] = [
  { value: 'off_topic', label: 'Не по теме' },
  { value: 'incomplete', label: 'Неполный ответ' },
  { value: 'excessive', label: 'Слишком много лишнего' },
  { value: 'other', label: 'Другое' }
]

const INTERRUPTED_TEXT = '⚠ Генерация была прервана. Попробуйте задать вопрос снова.'

function toServerMsgs(messages: ServerChatMessage[]): ChatMsg[] {
  return messages.map((m) => {
    // Сообщение загружено с сервера (не стримится сейчас). Пустой ответ
    // ассистента или «pending» означает прерванную генерацию (перезагрузка/
    // обрыв) — показываем понятный текст ошибки, а не вечный индикатор.
    const interrupted =
      m.role === 'assistant' && !m.is_error && (m.pending === true || !(m.content || '').trim())
    return {
      mid: m.mid,
      role: m.role,
      content: interrupted ? INTERRUPTED_TEXT : (m.content || ''),
      isError: m.is_error || interrupted,
      usedDocs: m.used_docs,
      totalTokens: m.total_tokens,
      promptTokens: m.prompt_tokens,
      completionTokens: m.completion_tokens,
      latencyMs: m.latency_ms,
      rewrittenQuery: m.rewritten_query,
      feedback: m.feedback
    }
  })
}

export default function ChatPanel() {
  const [chats, setChats] = useState<ChatSummary[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMsg[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [feedbackFor, setFeedbackFor] = useState<string | null>(null)
  const endRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  const scrollToBottom = useCallback(() => {
    requestAnimationFrame(() => endRef.current?.scrollIntoView({ behavior: 'auto' }))
  }, [])

  const refreshChats = useCallback(async () => {
    try {
      const list = await listChats()
      setChats(list)
      return list
    } catch {
      return []
    }
  }, [])

  const openChat = useCallback(async (id: string) => {
    setActiveId(id)
    try {
      const chat = await getChat(id)
      setMessages(toServerMsgs(chat.messages || []))
      scrollToBottom()
    } catch {
      setMessages([])
    }
  }, [scrollToBottom])

  // Первичная загрузка: список диалогов, выбрать первый или создать новый.
  useEffect(() => {
    ;(async () => {
      const list = await refreshChats()
      if (list.length > 0) {
        await openChat(list[0].id)
      } else {
        try {
          const c = await createChat()
          await refreshChats()
          setActiveId(c.id)
          setMessages([])
        } catch { /* ignore */ }
      }
    })()
  }, [refreshChats, openChat])

  const handleNewChat = useCallback(async () => {
    try {
      const c = await createChat()
      await refreshChats()
      setActiveId(c.id)
      setMessages([])
    } catch (e) {
      toast.error('Не удалось создать диалог')
    }
  }, [refreshChats])

  const handleDeleteChat = useCallback(async (id: string) => {
    try {
      await deleteChat(id)
      const list = await refreshChats()
      if (id === activeId) {
        if (list.length > 0) await openChat(list[0].id)
        else { setActiveId(null); setMessages([]) }
      }
    } catch {
      toast.error('Не удалось удалить диалог')
    }
  }, [activeId, refreshChats, openChat])

  const updateLastAssistant = useCallback((patch: Partial<ChatMsg>) => {
    setMessages((prev) => {
      const next = [...prev]
      for (let i = next.length - 1; i >= 0; i--) {
        if (next[i].role === 'assistant') {
          next[i] = { ...next[i], ...patch }
          break
        }
      }
      return next
    })
  }, [])

  const appendToLastAssistant = useCallback((chunk: string) => {
    setMessages((prev) => {
      const next = [...prev]
      for (let i = next.length - 1; i >= 0; i--) {
        if (next[i].role === 'assistant') {
          next[i] = { ...next[i], content: next[i].content + chunk }
          break
        }
      }
      return next
    })
    scrollToBottom()
  }, [scrollToBottom])

  const handleSend = useCallback(async () => {
    const question = input.trim()
    if (!question || loading || !activeId) return
    setInput('')
    setLoading(true)

    const state = useSettingsStore.getState()
    const mode = state.querySettings.mode
    const historyTurns = state.querySettings.history_turns || 0

    const priorTurns = messages.filter((m) => !m.isError)
    const willRewrite = state.rewriteEnabled && priorTurns.length > 0

    // Плейсхолдер ассистента показываем СРАЗУ (до реврайта), со статусом —
    // иначе во время реврайта/поиска чат выглядит «мёртвым».
    setMessages((prev) => [
      ...prev,
      { role: 'user', content: question },
      { role: 'assistant', content: '', streaming: true, status: willRewrite ? 'analyzing' : 'searching' }
    ])
    scrollToBottom()

    // Реврайт уточняющего вопроса для поиска (если включён и есть история).
    let searchQuery = question
    if (willRewrite) {
      try {
        const rw = await rewriteQuery(
          question,
          priorTurns.slice(-(historyTurns > 0 ? historyTurns : 3) * 2).map((m) => ({ role: m.role, content: m.content })),
          state.rewritePrompt,
          historyTurns
        )
        if (rw && rw.rewritten) searchQuery = rw.rewritten
      } catch { /* fall back to original */ }
      updateLastAssistant({
        status: 'searching',
        rewrittenQuery: searchQuery !== question ? searchQuery : undefined
      })
    }

    const controller = new AbortController()
    abortRef.current = controller
    try {
      await sendChatMessageStream(
        activeId,
        { message: question, search_query: searchQuery, mode, history_turns: historyTurns, user_prompt: state.querySettings.user_prompt || undefined },
        {
          onMeta: (mid) => updateLastAssistant({ mid: mid || undefined }),
          onReferences: (docs) => updateLastAssistant({ usedDocs: docs, status: 'generating' }),
          onChunk: (text) => appendToLastAssistant(text),
          onUsage: (u) => updateLastAssistant({
            totalTokens: u.total_tokens, promptTokens: u.prompt_tokens ?? undefined, completionTokens: u.completion_tokens
          }),
          onError: (err) => updateLastAssistant({ content: err, isError: true, streaming: false }),
          onDone: (latency) => updateLastAssistant({ latencyMs: latency ?? undefined, streaming: false })
        },
        controller.signal
      )
    } catch (e) {
      // Прерывание пользователем (кнопка «Стоп») — это не ошибка: оставляем
      // частичный ответ. Прочие исключения помечаем ошибкой.
      if (controller.signal.aborted) {
        updateLastAssistant({ streaming: false })
      } else {
        updateLastAssistant({ isError: true, streaming: false })
      }
    } finally {
      setLoading(false)
      abortRef.current = null
      // Гарантируем терминальное состояние: если стрим завершился без события
      // done/error (например, соединение оборвалось), сообщение не должно
      // навсегда остаться со спиннером.
      updateLastAssistant({ streaming: false })
      refreshChats()
      scrollToBottom()
    }
  }, [input, loading, activeId, messages, updateLastAssistant, appendToLastAssistant, refreshChats, scrollToBottom])

  const handleStop = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  const submitFeedback = useCallback(async (mid: string, rating: 'positive' | 'negative', reason?: string, comment?: string) => {
    try {
      await setChatFeedback(activeId!, mid, { rating, reason: reason || null, comment: comment || null })
      setMessages((prev) => prev.map((m) => (m.mid === mid ? { ...m, feedback: rating } : m)))
      setFeedbackFor(null)
    } catch (e: any) {
      toast.error('Не удалось сохранить оценку')
    }
  }, [activeId])

  return (
    <div className="flex size-full gap-3 p-3 overflow-hidden">
      {/* Сайдбар диалогов */}
      <div className="flex w-60 shrink-0 flex-col gap-2 overflow-hidden rounded-lg border bg-card/40 p-2">
        <Button onClick={handleNewChat} size="sm" className="w-full">
          <PlusIcon className="size-4" /> Новый диалог
        </Button>
        <div className="flex flex-col gap-1 overflow-auto">
          {chats.map((c) => (
            <div
              key={c.id}
              className={cn(
                'group flex items-center gap-2 rounded-md px-2 py-1.5 text-sm cursor-pointer',
                c.id === activeId ? 'bg-primary/15 text-foreground' : 'hover:bg-accent/40'
              )}
              onClick={() => openChat(c.id)}
            >
              <MessageSquareIcon className="size-3.5 shrink-0 opacity-60" />
              <span className="flex-1 truncate">{c.title || 'Диалог'}</span>
              <button
                className="opacity-0 group-hover:opacity-100 hover:text-destructive"
                onClick={(e) => { e.stopPropagation(); handleDeleteChat(c.id) }}
                title="Удалить"
              >
                <Trash2Icon className="size-3.5" />
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* Беседа */}
      <div className="flex grow flex-col gap-3 overflow-hidden">
        <div className="bg-card/60 relative grow overflow-auto rounded-lg border p-3">
          {messages.length === 0 ? (
            <div className="text-muted-foreground flex h-full items-center justify-center text-lg">
              Задайте вопрос — система ответит по доступным вам документам.
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              {messages.map((m, idx) => {
                const cot = parseCOTContent(m.content)
                const mwe: MessageWithError = {
                  id: m.mid || `m-${idx}`,
                  role: m.role,
                  content: m.content,
                  isError: m.isError,
                  isThinking: m.streaming ? cot.isThinking : false,
                  thinkingContent: cot.thinkingContent,
                  displayContent: m.role === 'assistant' ? cot.displayContent : m.content,
                  thinkingTime: null
                }
                const showStatus = m.role === 'assistant' && m.streaming && !m.content.trim()
                return (
                  <div key={mwe.id} className={cn('group flex flex-col', m.role === 'user' ? 'items-end' : 'items-start')}>
                    {showStatus ? (
                      <div className="flex w-[95%] items-center gap-2 rounded-lg bg-muted px-4 py-2 text-sm text-muted-foreground">
                        <LoaderIcon className="size-4 animate-spin" />
                        <span>{STATUS_LABELS[m.status || 'searching']}</span>
                      </div>
                    ) : (
                      <ChatMessage message={mwe} />
                    )}
                    {/* Копирование текста вопроса пользователя (по наведению) */}
                    {m.role === 'user' && m.content && (
                      <div className="mt-0.5 flex text-xs text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100">
                        <CopyButton text={m.content} withLabel />
                      </div>
                    )}
                    {m.role === 'assistant' && !m.streaming && !m.isError && (
                      <div className="mt-1 flex w-[95%] flex-wrap items-center gap-3 text-xs text-muted-foreground">
                        <CopyButton text={m.content} />
                        {m.usedDocs && m.usedDocs.length > 0 && (
                          <span className="flex items-center gap-1" title={m.usedDocs.join('\n')}>
                            <FileTextIcon className="size-3" />{m.usedDocs.length} док.
                          </span>
                        )}
                        {typeof m.latencyMs === 'number' && (
                          <span className="flex items-center gap-1"><ClockIcon className="size-3" />{(m.latencyMs / 1000).toFixed(1)}с</span>
                        )}
                        {typeof m.totalTokens === 'number' && (
                          <span className="flex items-center gap-1"><HashIcon className="size-3" />{m.totalTokens.toLocaleString('ru-RU')}</span>
                        )}
                        {m.mid && (
                          <span className="flex items-center gap-1">
                            <button
                              className={cn('rounded p-0.5 hover:text-primary', m.feedback === 'positive' && 'text-primary')}
                              onClick={() => submitFeedback(m.mid!, 'positive')}
                              title="Хороший ответ"
                            >
                              <ThumbsUpIcon className="size-3.5" />
                            </button>
                            <button
                              className={cn('rounded p-0.5 hover:text-destructive', m.feedback === 'negative' && 'text-destructive')}
                              onClick={() => setFeedbackFor(feedbackFor === m.mid ? null : m.mid!)}
                              title="Плохой ответ"
                            >
                              <ThumbsDownIcon className="size-3.5" />
                            </button>
                          </span>
                        )}
                        {feedbackFor === m.mid && (
                          <span className="flex flex-wrap items-center gap-1">
                            {FEEDBACK_REASONS.map((r) => (
                              <button
                                key={r.value}
                                className="rounded border px-1.5 py-0.5 hover:bg-accent/40"
                                onClick={() => {
                                  if (r.value === 'other') {
                                    const c = window.prompt('Комментарий (что не так):') || ''
                                    if (c.trim()) submitFeedback(m.mid!, 'negative', 'other', c.trim())
                                  } else {
                                    submitFeedback(m.mid!, 'negative', r.value)
                                  }
                                }}
                              >
                                {r.label}
                              </button>
                            ))}
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
              <div ref={endRef} className="pb-1" />
            </div>
          )}
        </div>

        <form
          className="flex shrink-0 items-center gap-2"
          onSubmit={(e) => { e.preventDefault(); handleSend() }}
        >
          <Input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Введите вопрос…"
            disabled={loading || !activeId}
            className="flex-1"
          />
          {loading ? (
            <Button type="button" variant="outline" size="sm" onClick={handleStop} title="Остановить ответ">
              <SquareIcon className="size-4" /> Стоп
            </Button>
          ) : (
            <Button type="submit" disabled={!activeId} size="sm">
              <SendIcon className="size-4" /> Отправить
            </Button>
          )}
        </form>
      </div>
    </div>
  )
}
