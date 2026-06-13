import { useState } from 'react'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/Tabs'
import ModelSettings from '@/features/ModelSettings'
import ProviderTesting from '@/features/ProviderTesting'
import QuerySettings from '@/components/retrieval/QuerySettings'
import PromptsSettings from '@/components/settings/PromptsSettings'
import { cn } from '@/lib/utils'

function SubTab({ value, current, children }: { value: string; current: string; children: React.ReactNode }) {
  return (
    <TabsTrigger
      value={value}
      className={cn(
        'cursor-pointer px-3 py-1.5 text-sm transition-all rounded-md',
        current === value ? '!bg-primary !text-primary-foreground' : 'hover:bg-background/60'
      )}
    >
      {children}
    </TabsTrigger>
  )
}

export default function SettingsPage() {
  const [sub, setSub] = useState('providers')

  return (
    <div className="flex h-full w-full flex-col overflow-hidden">
      <Tabs value={sub} onValueChange={setSub} className="flex h-full w-full flex-col">
        <div className="flex justify-center border-b border-border/60 bg-sidebar/40 py-2">
          <TabsList className="gap-2 bg-transparent">
            <SubTab value="providers" current={sub}>Провайдеры LLM</SubTab>
            <SubTab value="testing" current={sub}>Тестирование</SubTab>
            <SubTab value="query" current={sub}>Параметры запроса</SubTab>
            <SubTab value="prompts" current={sub}>Промпты</SubTab>
          </TabsList>
        </div>
        <div className="grow overflow-auto">
          {sub === 'providers' && <ModelSettings />}
          {sub === 'testing' && <ProviderTesting />}
          {sub === 'query' && (
            <div className="flex justify-center p-6">
              <QuerySettings />
            </div>
          )}
          {sub === 'prompts' && <PromptsSettings />}
        </div>
      </Tabs>
    </div>
  )
}
