import { useCallback, useState } from 'react'
import { toast } from 'sonner'
import { CheckCircle2, XCircle, AlertTriangle, Loader2, PlayIcon } from 'lucide-react'
import Button from '@/components/ui/Button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle
} from '@/components/ui/Card'
import { testProviders, type ProviderTestResult } from '@/api/userSettings'
import { cn, errorMessage } from '@/lib/utils'

const KIND_LABELS: Record<ProviderTestResult['kind'], string> = {
  chat: 'LLM',
  embedding: 'Эмбеддинги',
  rerank: 'Реранкер'
}

function StatusIcon({ result }: { result: ProviderTestResult }) {
  if (result.ok && result.warning) {
    return <AlertTriangle className="h-5 w-5 shrink-0 text-amber-500" />
  }
  if (result.ok) {
    return <CheckCircle2 className="h-5 w-5 shrink-0 text-emerald-500" />
  }
  return <XCircle className="h-5 w-5 shrink-0 text-red-500" />
}

function ResultRow({ result }: { result: ProviderTestResult }) {
  const codeText = result.status_code != null ? `HTTP ${result.status_code}` : 'нет ответа'
  return (
    <div className="border-border/60 flex items-start gap-3 border-b py-3 last:border-b-0">
      <StatusIcon result={result} />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium">{result.label}</span>
          <span className="bg-muted text-muted-foreground rounded px-1.5 py-0.5 text-[11px]">
            {KIND_LABELS[result.kind]}
          </span>
        </div>
        <span className="text-muted-foreground truncate text-xs" title={result.host}>
          {result.model} · {result.host}
        </span>
        {result.error && <span className="text-xs text-red-500">{result.error}</span>}
        {result.warning && <span className="text-xs text-amber-500">{result.warning}</span>}
      </div>
      <div className="flex shrink-0 flex-col items-end gap-0.5 text-right">
        <span
          className={cn(
            'text-sm font-medium',
            result.ok ? 'text-emerald-500' : 'text-red-500'
          )}
        >
          {codeText}
        </span>
        <span className="text-muted-foreground text-xs">{result.latency_ms} мс</span>
      </div>
    </div>
  )
}

export default function ProviderTesting() {
  const [running, setRunning] = useState(false)
  const [results, setResults] = useState<ProviderTestResult[] | null>(null)

  const run = useCallback(async () => {
    setRunning(true)
    try {
      const data = await testProviders()
      setResults(data.results)
      if (data.total === 0) {
        toast.info('Не найдено настроенных провайдеров для проверки')
      } else if (data.ok === data.total) {
        toast.success(`Все провайдеры доступны (${data.ok}/${data.total})`)
      } else {
        toast.warning(`Доступно ${data.ok} из ${data.total}. Подробности ниже.`)
      }
    } catch (err) {
      toast.error('Не удалось выполнить проверку: ' + errorMessage(err))
    } finally {
      setRunning(false)
    }
  }, [])

  return (
    <div className="flex justify-center overflow-auto p-6">
      <div className="flex w-full max-w-2xl flex-col gap-6 pb-12">
        <Card>
          <CardHeader>
            <CardTitle>Тестирование провайдеров</CardTitle>
            <CardDescription>
              Проверяет доступность всех настроенных сервисов (LLM, эмбеддинги, реранкер), у которых
              задан ключ или локальный адрес. Для каждого показывается код ответа и время отклика.
              LLM проверяется <b>без расхода токенов</b> (каталог моделей), эмбеддинги и реранкер —
              пробным запросом (затраты ничтожны). Адреса берутся только из сохранённой конфигурации
              сервера.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <div>
              <Button onClick={run} disabled={running}>
                {running ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Проверка...
                  </>
                ) : (
                  <>
                    <PlayIcon className="mr-2 h-4 w-4" />
                    Проверить провайдеров
                  </>
                )}
              </Button>
            </div>

            {results && results.length > 0 && (
              <div className="flex flex-col">
                {results.map((r, i) => (
                  <ResultRow key={`${r.kind}-${r.label}-${i}`} result={r} />
                ))}
              </div>
            )}

            {results && results.length === 0 && (
              <p className="text-muted-foreground text-sm">
                Нет настроенных провайдеров для проверки. Задайте модель и ключ на вкладке
                «Провайдеры LLM».
              </p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
