import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { useSettingsStore } from '@/stores/settings'
import { DEFAULT_REWRITE_PROMPT } from '@/stores/settings'
import { adminGetGlobalPrompt, adminSetGlobalPrompt } from '@/api/lightrag'
import { errorMessage } from '@/lib/utils'
import Textarea from '@/components/ui/Textarea'
import Button from '@/components/ui/Button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle
} from '@/components/ui/Card'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue
} from '@/components/ui/Select'
import { RotateCcw } from 'lucide-react'

type PromptKind = 'global' | 'answer' | 'rewrite'

export default function PromptsSettings() {
  const [kind, setKind] = useState<PromptKind>('global')

  const querySettings = useSettingsStore((state) => state.querySettings)
  const rewritePrompt = useSettingsStore((state) => state.rewritePrompt)

  // Общий промпт — серверный (грузится с /admin/global_prompt). Эта вкладка
  // доступна только администратору, поэтому вызов админского эндпоинта здесь корректен.
  const [globalPrompt, setGlobalPrompt] = useState('')
  const [globalDefault, setGlobalDefault] = useState('')
  const [globalSaving, setGlobalSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    adminGetGlobalPrompt()
      .then((d) => {
        if (!cancelled) {
          setGlobalPrompt(d.prompt ?? '')
          setGlobalDefault(d.default ?? '')
        }
      })
      .catch(() => {
        /* недоступно (не админ / сеть) — оставляем пустым */
      })
    return () => {
      cancelled = true
    }
  }, [])

  const value =
    kind === 'answer'
      ? querySettings.user_prompt || ''
      : kind === 'rewrite'
        ? rewritePrompt
        : globalPrompt

  const onChange = (v: string) => {
    if (kind === 'answer') {
      useSettingsStore.getState().updateQuerySettings({ user_prompt: v })
    } else if (kind === 'rewrite') {
      useSettingsStore.getState().setRewritePrompt(v)
    } else {
      setGlobalPrompt(v)
    }
  }

  const onReset = () => {
    if (kind === 'answer') {
      useSettingsStore.getState().updateQuerySettings({ user_prompt: '' })
    } else if (kind === 'rewrite') {
      useSettingsStore.getState().setRewritePrompt(DEFAULT_REWRITE_PROMPT)
    } else {
      // Подставляем заводской текст в поле — администратор сохраняет вручную.
      setGlobalPrompt(globalDefault)
    }
  }

  const onSaveGlobal = async () => {
    setGlobalSaving(true)
    try {
      const res = await adminSetGlobalPrompt(globalPrompt)
      setGlobalPrompt(res.prompt ?? '')
      toast.success('Общий промпт сохранён — применяется сразу, без перезапуска')
    } catch (err) {
      toast.error('Не удалось сохранить общий промпт: ' + errorMessage(err))
    } finally {
      setGlobalSaving(false)
    }
  }

  const desc =
    kind === 'global'
      ? 'Общий (базовый) промпт оператора — применяется ко ВСЕМ ответам (все отделы, администратор и пользовательский чат). Если у отдела задан собственный промпт ответа, он ЗАМЕНЯЕТ общий. Хранится на сервере; изменения вступают в силу сразу, перезапуск не нужен.'
      : kind === 'answer'
        ? 'Дополнительный промпт вывода. Подмешивается к ответу модели — задаёт роль, тон и формат. Пусто — поведение по умолчанию. Хранится в этом браузере и переопределяет общий промпт.'
        : 'Промпт реврайта. Переписывает уточняющий вопрос в самостоятельный поисковый запрос. Обязательны плейсхолдеры {history} и {question}.'

  return (
    <div className="flex justify-center overflow-auto p-6">
      <Card className="w-full max-w-3xl">
        <CardHeader>
          <CardTitle>Промпты</CardTitle>
          <CardDescription>
            Выберите промпт для редактирования. «Общий промпт» сохраняется на сервере по кнопке;
            остальные хранятся локально и применяются сразу.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <Select value={kind} onValueChange={(v) => setKind(v as PromptKind)}>
              <SelectTrigger className="h-9 w-72 cursor-pointer">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  <SelectItem value="global">Общий промпт (сервер)</SelectItem>
                  <SelectItem value="answer">Промпт ответа (вывод)</SelectItem>
                  <SelectItem value="rewrite">Промпт реврайта</SelectItem>
                </SelectGroup>
              </SelectContent>
            </Select>
            <Button variant="outline" size="sm" onClick={onReset}>
              <RotateCcw className="mr-1 size-3" /> Сбросить
            </Button>
            {kind === 'global' && (
              <Button size="sm" onClick={onSaveGlobal} disabled={globalSaving}>
                {globalSaving ? 'Сохранение...' : 'Сохранить на сервере'}
              </Button>
            )}
          </div>
          <p className="text-muted-foreground text-xs">{desc}</p>
          <Textarea
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder={
              kind === 'global'
                ? globalDefault || 'Базовый промпт оператора (роль, правила ответа)'
                : kind === 'answer'
                  ? 'Например: «Отвечай кратко, по делу, на русском»'
                  : DEFAULT_REWRITE_PROMPT
            }
            className="min-h-[280px] font-mono text-xs"
          />
          {kind === 'global' && !globalPrompt.trim() && (
            <p className="text-amber-500 text-xs">
              Общий промпт пуст — базовые правила оператора применяться не будут (кроме промптов
              отделов). Нажмите «Сбросить», чтобы вернуть заводской текст.
            </p>
          )}
          {kind === 'rewrite' && !(/\{history\}/.test(value) && /\{question\}/.test(value)) && (
            <p className="text-amber-500 text-xs">
              В промпте реврайта рекомендуются плейсхолдеры {'{history}'} и {'{question}'} — иначе история
              и вопрос будут добавлены автоматически в конец.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
