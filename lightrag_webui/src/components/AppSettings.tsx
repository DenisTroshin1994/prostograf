import { useState, useCallback } from 'react'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover'
import Button from '@/components/ui/Button'
import Checkbox from '@/components/ui/Checkbox'
import { useSettingsStore } from '@/stores/settings'
import { SettingsIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

interface AppSettingsProps {
  className?: string
}

export default function AppSettings({ className }: AppSettingsProps) {
  const [opened, setOpened] = useState<boolean>(false)

  const enableHealthCheck = useSettingsStore.use.enableHealthCheck()
  const setEnableHealthCheck = useSettingsStore.use.setEnableHealthCheck()

  const handleHealthCheckChange = useCallback(
    (checked: boolean | 'indeterminate') => {
      setEnableHealthCheck(checked === true)
    },
    [setEnableHealthCheck]
  )

  return (
    <Popover open={opened} onOpenChange={setOpened}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className={cn('h-9 w-9', className)}
          tooltip="Настройки"
          side="bottom"
        >
          <SettingsIcon className="h-5 w-5" />
        </Button>
      </PopoverTrigger>
      <PopoverContent side="bottom" align="end" className="w-64">
        <div className="flex flex-col gap-4">
          <label className="flex cursor-pointer items-center gap-2 text-sm font-medium">
            <Checkbox checked={enableHealthCheck} onCheckedChange={handleHealthCheckChange} />
            Проверка состояния сервера
          </label>
        </div>
      </PopoverContent>
    </Popover>
  )
}
