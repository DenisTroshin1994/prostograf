import axios from 'axios'
import { backendBaseUrl } from '@/lib/constants'
import { useSettingsStore } from '@/stores/settings'

// Отдельный экземпляр axios, чтобы не зависеть от внутреннего экземпляра lightrag.ts
const client = axios.create({
  baseURL: backendBaseUrl,
  headers: { 'Content-Type': 'application/json' }
})

client.interceptors.request.use((config) => {
  const apiKey = useSettingsStore.getState().apiKey
  const token = localStorage.getItem('LIGHTRAG-API-TOKEN')
  if (token) {
    config.headers['Authorization'] = `Bearer ${token}`
  }
  if (apiKey) {
    config.headers['X-API-Key'] = apiKey
  }
  return config
})

export type ProviderName =
  | 'openrouter'
  | 'deepseek'
  | 'openai'
  | 'openai_compatible'
  | 'ollama'
  | 'lmstudio'

export type RerankBinding = 'cohere' | 'jina' | 'aliyun'

type ProviderSettings = { model: string; host: string; has_key: boolean }

export type UserLLMSettings = {
  provider: ProviderName
  openrouter: ProviderSettings
  deepseek: ProviderSettings
  openai: ProviderSettings
  openai_compatible: ProviderSettings
  ollama: ProviderSettings
  lmstudio: ProviderSettings
  embedding: { model: string; host: string; dim: number | null; has_key: boolean }
  rerank: { enabled: boolean; binding: RerankBinding; model: string; host: string; has_key: boolean }
  chunk: { size: number; overlap: number }
  rerank_bindings: RerankBinding[]
  providers: ProviderName[]
  fixed_host_providers: ProviderName[]
  custom_host_providers: ProviderName[]
  local_providers: ProviderName[]
  hosts: Partial<Record<ProviderName, string>>
  default_hosts: Partial<Record<ProviderName, string>>
  effective: {
    llm_binding: string
    llm_binding_host: string
    llm_model: string
    embedding_binding_host: string
    embedding_model: string
    embedding_dim: number | null
    rerank_enabled: boolean
    rerank_binding: string
    rerank_model: string | null
    chunk_size: number
    chunk_overlap_size: number
  }
}

type ProviderPayloadCreds = { api_key: string; model: string; host: string }

export type UserLLMSettingsPayload = {
  provider: ProviderName
  openrouter: ProviderPayloadCreds
  deepseek: ProviderPayloadCreds
  openai: ProviderPayloadCreds
  openai_compatible: ProviderPayloadCreds
  ollama: ProviderPayloadCreds
  lmstudio: ProviderPayloadCreds
  embedding: { api_key: string; model: string; host: string; dim: number | null }
  rerank: { enabled: boolean; binding: RerankBinding; model: string; host: string; api_key: string }
  chunk: { size: number; overlap: number }
}

export type ProviderTestResult = {
  kind: 'chat' | 'embedding' | 'rerank'
  label: string
  host: string
  model: string
  ok: boolean
  status_code: number | null
  latency_ms: number
  error: string | null
  warning: string | null
}

export type ProviderTestResponse = {
  results: ProviderTestResult[]
  total: number
  ok: number
}

export const getUserLLMSettings = async (): Promise<UserLLMSettings> => {
  const response = await client.get('/user_llm_settings')
  return response.data
}

export const saveUserLLMSettings = async (
  payload: UserLLMSettingsPayload
): Promise<{ status: string; message: string }> => {
  const response = await client.post('/user_llm_settings', payload)
  return response.data
}

export const testProviders = async (): Promise<ProviderTestResponse> => {
  // Проверка ходит к внешним сервисам — даём щедрый таймаут поверх axios-дефолта.
  const response = await client.post('/user_llm_settings/test', undefined, { timeout: 60000 })
  return response.data
}

export const waitForServerRestart = async (
  timeoutMs = 120000,
  intervalMs = 2000
): Promise<boolean> => {
  const deadline = Date.now() + timeoutMs
  // Сначала даём серверу время уйти в перезапуск
  await new Promise((resolve) => setTimeout(resolve, 3000))
  while (Date.now() < deadline) {
    try {
      const response = await client.get('/health', { timeout: 3000 })
      if (response.status === 200) {
        return true
      }
    } catch {
      // сервер ещё не поднялся — ждём дальше
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs))
  }
  return false
}
