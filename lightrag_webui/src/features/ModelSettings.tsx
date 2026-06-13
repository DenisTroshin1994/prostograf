import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import Checkbox from '@/components/ui/Checkbox'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle
} from '@/components/ui/Card'
import Separator from '@/components/ui/Separator'
import {
  getUserLLMSettings,
  saveUserLLMSettings,
  waitForServerRestart,
  type ProviderName,
  type RerankBinding,
  type UserLLMSettings
} from '@/api/userSettings'
import { cn, errorMessage } from '@/lib/utils'

// ── Метаданные провайдеров LLM (совпадают с бэкендом user_llm_settings.py) ──
const ALL_PROVIDERS: ProviderName[] = [
  'openrouter',
  'deepseek',
  'openai',
  'openai_compatible',
  'ollama',
  'lmstudio'
]
// Провайдеры с вводимым адресом сервиса (показываем поле host).
const CUSTOM_HOST_PROVIDERS: ProviderName[] = ['openai_compatible', 'ollama', 'lmstudio']
// Локальные движки — API-ключ не обязателен.
const LOCAL_PROVIDERS: ProviderName[] = ['ollama', 'lmstudio']
const DEFAULT_HOSTS: Partial<Record<ProviderName, string>> = {
  ollama: 'http://host.docker.internal:11434/v1',
  lmstudio: 'http://host.docker.internal:1234/v1'
}

const PROVIDER_LABELS: Record<ProviderName, string> = {
  openrouter: 'OpenRouter',
  deepseek: 'DeepSeek',
  openai: 'OpenAI',
  openai_compatible: 'OpenAI-совместимый',
  ollama: 'Ollama',
  lmstudio: 'LM Studio'
}
const DEFAULT_MODELS: Record<ProviderName, string> = {
  openrouter: '',
  deepseek: 'deepseek-chat',
  openai: 'gpt-4o-mini',
  openai_compatible: '',
  ollama: '',
  lmstudio: ''
}
const MODEL_HINTS: Record<ProviderName, string> = {
  openrouter: 'Например: anthropic/claude-sonnet-4.6, deepseek/deepseek-chat-v3-0324, openai/gpt-4o-mini',
  deepseek: 'deepseek-chat или deepseek-reasoner',
  openai: 'Например: gpt-4o-mini, gpt-4o, o3-mini',
  openai_compatible:
    'Имя модели, как его ожидает ваш сервер (vLLM, Groq, Together, Mistral, llama.cpp и т.п.)',
  ollama: 'Имя модели Ollama, например: llama3.1, qwen2.5, gemma2 (модель должна быть загружена)',
  lmstudio: 'Идентификатор загруженной в LM Studio модели'
}
const MODEL_PLACEHOLDERS: Record<ProviderName, string> = {
  openrouter: 'vendor/model',
  deepseek: 'deepseek-chat',
  openai: 'gpt-4o-mini',
  openai_compatible: 'model-name',
  ollama: 'llama3.1',
  lmstudio: 'model-name'
}
const HOST_HINTS: Partial<Record<ProviderName, string>> = {
  openai_compatible:
    'OpenAI-совместимый базовый URL (обычно заканчивается на /v1). Напр.: https://api.groq.com/openai/v1',
  ollama:
    'По умолчанию http://host.docker.internal:11434/v1 — Ollama на хост-машине, доступная из контейнера.',
  lmstudio:
    'По умолчанию http://host.docker.internal:1234/v1 — локальный сервер LM Studio (Developer → Start Server).'
}
const KEY_PLACEHOLDERS: Partial<Record<ProviderName, string>> = {
  openrouter: 'sk-or-...',
  deepseek: 'sk-...',
  openai: 'sk-...',
  openai_compatible: 'ключ вашего провайдера'
}

type ProviderForm = { key: string; model: string; host: string }

type FormState = {
  provider: ProviderName
  providers: Record<ProviderName, ProviderForm>
  embeddingKey: string
  embeddingModel: string
  embeddingHost: string
  embeddingDim: string
  rerankEnabled: boolean
  rerankBinding: RerankBinding
  rerankModel: string
  rerankHost: string
  rerankKey: string
  chunkSize: string
  chunkOverlap: string
}

function emptyProviders(): Record<ProviderName, ProviderForm> {
  return {
    openrouter: { key: '', model: DEFAULT_MODELS.openrouter, host: '' },
    deepseek: { key: '', model: DEFAULT_MODELS.deepseek, host: '' },
    openai: { key: '', model: DEFAULT_MODELS.openai, host: '' },
    openai_compatible: { key: '', model: '', host: '' },
    ollama: { key: '', model: '', host: DEFAULT_HOSTS.ollama || '' },
    lmstudio: { key: '', model: '', host: DEFAULT_HOSTS.lmstudio || '' }
  }
}

const emptyForm: FormState = {
  provider: 'openrouter',
  providers: emptyProviders(),
  embeddingKey: '',
  embeddingModel: '',
  embeddingHost: 'https://openrouter.ai/api/v1',
  embeddingDim: '',
  rerankEnabled: false,
  rerankBinding: 'cohere',
  rerankModel: 'rerank-v3.5',
  rerankHost: '',
  rerankKey: '',
  chunkSize: '1200',
  chunkOverlap: '100'
}

const RERANK_BINDING_LABELS: Record<RerankBinding, string> = {
  cohere: 'Cohere (/v2/rerank — Cohere, AITunnel, OpenRouter)',
  jina: 'Jina (/v1/rerank)',
  aliyun: 'Aliyun DashScope'
}

function Field({
  label,
  hint,
  children
}: {
  label: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-sm font-medium">{label}</label>
      {children}
      {hint && <p className="text-muted-foreground text-xs">{hint}</p>}
    </div>
  )
}

function KeyInput({
  value,
  onChange,
  hasSavedKey,
  placeholder
}: {
  value: string
  onChange: (value: string) => void
  hasSavedKey: boolean
  placeholder: string
}) {
  return (
    <Input
      type="password"
      autoComplete="off"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={hasSavedKey ? 'Ключ сохранён — оставьте пустым, чтобы не менять' : placeholder}
    />
  )
}

export default function ModelSettings() {
  const [form, setForm] = useState<FormState>(emptyForm)
  const [settings, setSettings] = useState<UserLLMSettings | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [restarting, setRestarting] = useState(false)

  const set = useCallback(<K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }))
  }, [])

  const setProvider = useCallback(
    (provider: ProviderName, field: keyof ProviderForm, value: string) => {
      setForm((prev) => ({
        ...prev,
        providers: {
          ...prev.providers,
          [provider]: { ...prev.providers[provider], [field]: value }
        }
      }))
    },
    []
  )

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await getUserLLMSettings()
      setSettings(data)
      const providers = emptyProviders()
      ALL_PROVIDERS.forEach((p) => {
        providers[p] = {
          key: '',
          model: data[p]?.model || DEFAULT_MODELS[p],
          host: data[p]?.host || DEFAULT_HOSTS[p] || ''
        }
      })
      setForm({
        provider: data.provider,
        providers,
        embeddingKey: '',
        embeddingModel: data.embedding.model || '',
        embeddingHost: data.embedding.host || 'https://openrouter.ai/api/v1',
        embeddingDim: data.embedding.dim ? String(data.embedding.dim) : '',
        rerankEnabled: !!data.rerank?.enabled,
        rerankBinding: data.rerank?.binding || 'cohere',
        rerankModel: data.rerank?.model || 'rerank-v3.5',
        rerankHost: data.rerank?.host || '',
        rerankKey: '',
        chunkSize: String(data.chunk?.size ?? 1200),
        chunkOverlap: String(data.chunk?.overlap ?? 100)
      })
    } catch (err) {
      toast.error('Не удалось загрузить настройки: ' + errorMessage(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const handleSave = useCallback(async () => {
    const prov = form.provider
    const active = form.providers[prov]
    const isCustomHost = CUSTOM_HOST_PROVIDERS.includes(prov)
    const isLocal = LOCAL_PROVIDERS.includes(prov)

    if (!active.model.trim()) {
      toast.error('Укажите модель для выбранного провайдера')
      return
    }
    if (isCustomHost) {
      const host = active.host.trim() || DEFAULT_HOSTS[prov] || ''
      if (!host) {
        toast.error('Укажите адрес сервиса (host) для выбранного провайдера')
        return
      }
      if (!/^https?:\/\//i.test(host)) {
        toast.error('Адрес сервиса должен начинаться с http:// или https://')
        return
      }
    }
    if (!isLocal && !active.key.trim() && !settings?.[prov]?.has_key) {
      toast.error('Укажите API-ключ для выбранного провайдера')
      return
    }

    const dim = form.embeddingDim.trim() ? Number(form.embeddingDim.trim()) : null
    if (dim !== null && (!Number.isInteger(dim) || dim <= 0)) {
      toast.error('Размерность эмбеддингов должна быть целым положительным числом')
      return
    }

    if (form.rerankEnabled) {
      if (!form.rerankModel.trim()) {
        toast.error('Укажите модель реранкера или выключите его')
        return
      }
      if (!form.rerankKey.trim() && !settings?.rerank?.has_key) {
        toast.error('Укажите API-ключ реранкера или выключите его')
        return
      }
    }

    const chunkSize = Number(form.chunkSize.trim())
    const chunkOverlap = Number(form.chunkOverlap.trim())
    if (!Number.isInteger(chunkSize) || chunkSize <= 0) {
      toast.error('Размер чанка должен быть целым положительным числом')
      return
    }
    if (!Number.isInteger(chunkOverlap) || chunkOverlap < 0 || chunkOverlap >= chunkSize) {
      toast.error('Перекрытие должно быть целым ≥ 0 и меньше размера чанка')
      return
    }

    const creds = (p: ProviderName) => ({
      api_key: form.providers[p].key.trim(),
      model: form.providers[p].model.trim(),
      host: form.providers[p].host.trim()
    })

    setSaving(true)
    try {
      const result = await saveUserLLMSettings({
        provider: prov,
        openrouter: creds('openrouter'),
        deepseek: creds('deepseek'),
        openai: creds('openai'),
        openai_compatible: creds('openai_compatible'),
        ollama: creds('ollama'),
        lmstudio: creds('lmstudio'),
        embedding: {
          api_key: form.embeddingKey.trim(),
          model: form.embeddingModel.trim(),
          host: form.embeddingHost.trim(),
          dim
        },
        rerank: {
          enabled: form.rerankEnabled,
          binding: form.rerankBinding,
          model: form.rerankModel.trim(),
          host: form.rerankHost.trim(),
          api_key: form.rerankKey.trim()
        },
        chunk: { size: chunkSize, overlap: chunkOverlap }
      })
      toast.info(result.message || 'Настройки сохранены, сервер перезапускается...')
      setRestarting(true)
      const ok = await waitForServerRestart()
      setRestarting(false)
      if (ok) {
        toast.success('Сервер перезапущен, настройки применены')
        await load()
      } else {
        toast.error('Сервер не ответил после перезапуска. Обновите страницу через минуту.')
      }
    } catch (err) {
      toast.error('Не удалось сохранить настройки: ' + errorMessage(err))
    } finally {
      setSaving(false)
      setRestarting(false)
    }
  }, [form, settings, load])

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-muted-foreground">Загрузка настроек...</p>
      </div>
    )
  }

  const busy = saving || restarting
  const prov = form.provider
  const active = form.providers[prov]
  const isCustomHost = CUSTOM_HOST_PROVIDERS.includes(prov)
  const isLocal = LOCAL_PROVIDERS.includes(prov)

  return (
    <div className="flex justify-center overflow-auto p-6">
      <div className="flex w-full max-w-2xl flex-col gap-6 pb-12">
        <Card>
          <CardHeader>
            <CardTitle>Языковая модель</CardTitle>
            <CardDescription>
              Выберите провайдера и модель. Поддерживаются OpenRouter, DeepSeek, OpenAI, любой
              OpenAI-совместимый сервис (vLLM, Groq, Together, Mistral, llama.cpp…), а также
              локальные движки Ollama и LM Studio. Настройки сохраняются на сервере и применяются
              после автоматического перезапуска — пересборка не нужна.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {ALL_PROVIDERS.map((value) => (
                <Button
                  key={value}
                  type="button"
                  variant={form.provider === value ? 'default' : 'outline'}
                  className={cn(form.provider === value && 'font-semibold')}
                  onClick={() => set('provider', value)}
                  disabled={busy}
                >
                  {PROVIDER_LABELS[value]}
                </Button>
              ))}
            </div>

            <Field label={`Модель · ${PROVIDER_LABELS[prov]}`} hint={MODEL_HINTS[prov]}>
              <Input
                value={active.model}
                onChange={(e) => setProvider(prov, 'model', e.target.value)}
                placeholder={MODEL_PLACEHOLDERS[prov]}
                disabled={busy}
              />
            </Field>

            {isCustomHost && (
              <Field label="Адрес сервиса (host)" hint={HOST_HINTS[prov]}>
                <Input
                  value={active.host}
                  onChange={(e) => setProvider(prov, 'host', e.target.value)}
                  placeholder={DEFAULT_HOSTS[prov] || 'https://.../v1'}
                  disabled={busy}
                />
              </Field>
            )}

            <Field
              label={`API-ключ · ${PROVIDER_LABELS[prov]}`}
              hint={
                isLocal ? 'Локальный движок — ключ не требуется, можно оставить пустым.' : undefined
              }
            >
              <KeyInput
                value={active.key}
                onChange={(v) => setProvider(prov, 'key', v)}
                hasSavedKey={!!settings?.[prov]?.has_key}
                placeholder={isLocal ? 'не требуется' : KEY_PLACEHOLDERS[prov] || 'API-ключ'}
              />
            </Field>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Эмбеддинги</CardTitle>
            <CardDescription>
              Сервис эмбеддингов (OpenAI-совместимый API). По умолчанию — OpenRouter.{' '}
              <span className="text-amber-500">
                Внимание: смена модели эмбеддингов делает уже проиндексированные документы
                несовместимыми — их придётся переиндексировать.
              </span>
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <Field label="Модель эмбеддингов" hint="Например: openai/text-embedding-3-large">
              <Input
                value={form.embeddingModel}
                onChange={(e) => set('embeddingModel', e.target.value)}
                placeholder="vendor/model"
                disabled={busy}
              />
            </Field>
            <Field label="API-ключ эмбеддингов">
              <KeyInput
                value={form.embeddingKey}
                onChange={(v) => set('embeddingKey', v)}
                hasSavedKey={!!settings?.embedding.has_key}
                placeholder="sk-or-..."
              />
            </Field>
            <Field
              label="Адрес сервиса (host)"
              hint="OpenAI-совместимый базовый URL. По умолчанию https://openrouter.ai/api/v1"
            >
              <Input
                value={form.embeddingHost}
                onChange={(e) => set('embeddingHost', e.target.value)}
                placeholder="https://openrouter.ai/api/v1"
                disabled={busy}
              />
            </Field>
            <Field
              label="Размерность векторов (необязательно)"
              hint="Например 3072 для text-embedding-3-large. Оставьте пустым, чтобы определить автоматически."
            >
              <Input
                value={form.embeddingDim}
                onChange={(e) => set('embeddingDim', e.target.value)}
                placeholder="3072"
                inputMode="numeric"
                disabled={busy}
              />
            </Field>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Реранкер</CardTitle>
            <CardDescription>
              Переупорядочивает найденные фрагменты по релевантности перед генерацией ответа —
              обычно повышает точность в режимах <code>mix</code>/<code>hybrid</code>. Использует
              отдельный сервис (Cohere-совместимый <code>/rerank</code>; модель вызывается на каждый
              запрос — это доп. расходы). По умолчанию выключен.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <label className="flex cursor-pointer items-center gap-2">
              <Checkbox
                checked={form.rerankEnabled}
                onCheckedChange={(c) => set('rerankEnabled', !!c)}
                disabled={busy}
              />
              <span className="text-sm font-medium">Включить реранкер</span>
            </label>
            {form.rerankEnabled && (
              <>
                <Field label="Провайдер (тип API)">
                  <select
                    value={form.rerankBinding}
                    onChange={(e) => set('rerankBinding', e.target.value as RerankBinding)}
                    disabled={busy}
                    className="border-input bg-background ring-offset-background focus-visible:ring-ring h-9 rounded-md border px-3 py-1 text-sm focus-visible:ring-2 focus-visible:outline-none disabled:opacity-50"
                  >
                    {(settings?.rerank_bindings || ['cohere', 'jina', 'aliyun']).map((b) => (
                      <option key={b} value={b}>
                        {RERANK_BINDING_LABELS[b as RerankBinding] || b}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Модель реранкера" hint="Например: rerank-v3.5, cohere/rerank-v3.5, jina-reranker-v2-base-multilingual">
                  <Input
                    value={form.rerankModel}
                    onChange={(e) => set('rerankModel', e.target.value)}
                    placeholder="rerank-v3.5"
                    disabled={busy}
                  />
                </Field>
                <Field
                  label="Адрес сервиса (host, необязательно)"
                  hint="Полный URL эндпоинта /rerank. Пусто — берётся адрес по умолчанию выбранного провайдера. Пример AITunnel: https://api.aitunnel.ru/v1/rerank"
                >
                  <Input
                    value={form.rerankHost}
                    onChange={(e) => set('rerankHost', e.target.value)}
                    placeholder="https://api.cohere.com/v2/rerank"
                    disabled={busy}
                  />
                </Field>
                <Field label="API-ключ реранкера">
                  <KeyInput
                    value={form.rerankKey}
                    onChange={(v) => set('rerankKey', v)}
                    hasSavedKey={!!settings?.rerank?.has_key}
                    placeholder="ключ провайдера реранкера"
                  />
                </Field>
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Чанкинг (разбиение документов)</CardTitle>
            <CardDescription>
              Как документы режутся на фрагменты при индексации.{' '}
              <span className="text-amber-500">
                Применяется только к НОВЫМ загружаемым документам — уже проиндексированные не
                меняются. Меньший размер — точнее поиск, но больше фрагментов.
              </span>
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <Field label="Размер чанка (токенов)" hint="По умолчанию 1200">
              <Input
                value={form.chunkSize}
                onChange={(e) => set('chunkSize', e.target.value)}
                placeholder="1200"
                inputMode="numeric"
                disabled={busy}
              />
            </Field>
            <Field label="Перекрытие чанков (токенов)" hint="По умолчанию 100. Должно быть меньше размера чанка.">
              <Input
                value={form.chunkOverlap}
                onChange={(e) => set('chunkOverlap', e.target.value)}
                placeholder="100"
                inputMode="numeric"
                disabled={busy}
              />
            </Field>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Текущая конфигурация</CardTitle>
          </CardHeader>
          <CardContent className="text-sm">
            {settings && (
              <div className="text-muted-foreground flex flex-col gap-1">
                <span>
                  LLM: {settings.effective.llm_model}{' '}
                  <span className="opacity-70">({settings.effective.llm_binding_host})</span>
                </span>
                <span>
                  Эмбеддинги: {settings.effective.embedding_model}{' '}
                  <span className="opacity-70">
                    ({settings.effective.embedding_binding_host}
                    {settings.effective.embedding_dim
                      ? `, размерность ${settings.effective.embedding_dim}`
                      : ''}
                    )
                  </span>
                </span>
                <span>
                  Реранкер:{' '}
                  {settings.effective.rerank_enabled
                    ? `включён (${settings.effective.rerank_binding}${settings.effective.rerank_model ? ', ' + settings.effective.rerank_model : ''})`
                    : 'выключен'}
                </span>
                <span>
                  Чанкинг: размер {settings.effective.chunk_size}, перекрытие{' '}
                  {settings.effective.chunk_overlap_size}
                </span>
              </div>
            )}
          </CardContent>
        </Card>

        <Separator />

        <div className="flex items-center justify-end gap-3">
          {restarting && (
            <span className="text-muted-foreground text-sm">
              Сервер перезапускается, подождите...
            </span>
          )}
          <Button onClick={handleSave} disabled={busy}>
            {saving ? 'Сохранение...' : 'Сохранить и применить'}
          </Button>
        </div>
      </div>
    </div>
  )
}
