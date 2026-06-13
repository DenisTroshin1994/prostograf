import Button from '@/components/ui/Button'
import { SiteInfo, webuiPrefix } from '@/lib/constants'
import { TabsList, TabsTrigger } from '@/components/ui/Tabs'
import { useSettingsStore } from '@/stores/settings'
import { useAuthStore } from '@/stores/state'
import { cn } from '@/lib/utils'
import { useTranslation } from 'react-i18next'
import { navigationService } from '@/services/navigation'
import { LogOutIcon } from 'lucide-react'
import Logo from '@/components/Logo'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip'
import StatusIndicator from '@/components/status/StatusIndicator'

interface NavigationTabProps {
  value: string
  currentTab: string
  children: React.ReactNode
}

function NavigationTab({ value, currentTab, children }: NavigationTabProps) {
  return (
    <TabsTrigger
      value={value}
      className={cn(
        'cursor-pointer px-2 py-1 transition-all',
        currentTab === value ? '!bg-primary !text-primary-foreground' : 'hover:bg-background/60'
      )}
    >
      {children}
    </TabsTrigger>
  )
}

function TabsNavigation() {
  const currentTab = useSettingsStore.use.currentTab()
  const { t } = useTranslation()

  return (
    <div className="flex h-8 self-center">
      <TabsList className="h-full gap-2">
        <NavigationTab value="retrieval" currentTab={currentTab}>
          {t('header.chat', 'Чат')}
        </NavigationTab>
        <NavigationTab value="documents" currentTab={currentTab}>
          {t('header.documents')}
        </NavigationTab>
        <NavigationTab value="knowledge-graph" currentTab={currentTab}>
          {t('header.knowledgeGraph')}
        </NavigationTab>
        <NavigationTab value="chats" currentTab={currentTab}>
          {t('header.history', 'История')}
        </NavigationTab>
        <NavigationTab value="admin-users" currentTab={currentTab}>
          {t('header.adminUsers', 'Отделы и пользователи')}
        </NavigationTab>
        <NavigationTab value="documentation" currentTab={currentTab}>
          {t('header.documentation', 'Документация')}
        </NavigationTab>
        <NavigationTab value="settings" currentTab={currentTab}>
          {t('header.modelSettings', 'Настройки')}
        </NavigationTab>
      </TabsList>
    </div>
  )
}

export default function SiteHeader() {
  const { t } = useTranslation()
  const { isGuestMode, username, webuiTitle, webuiDescription, appName } = useAuthStore()

  const handleLogout = () => {
    useAuthStore.getState().logout();
    navigationService.navigateToLogin();
  }

  return (
    <header className="border-border/60 bg-sidebar/95 supports-[backdrop-filter]:bg-sidebar/75 sticky top-0 z-50 flex h-10 w-full border-b px-4 backdrop-blur">
      <div className="min-w-[200px] w-auto flex items-center">
        <a href={webuiPrefix} className="flex items-center gap-2">
          <Logo className="size-5 text-primary" />
          <span className="font-serif font-bold md:inline-block">{appName || SiteInfo.name}</span>
        </a>
        <StatusIndicator className="ml-3 hidden md:flex" />
        {webuiTitle && (
          <div className="flex items-center">
            <span className="mx-1 text-xs text-muted-foreground">|</span>
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="font-medium text-sm cursor-default">
                    {webuiTitle}
                  </span>
                </TooltipTrigger>
                {webuiDescription && (
                  <TooltipContent side="bottom">
                    {webuiDescription}
                  </TooltipContent>
                )}
              </Tooltip>
            </TooltipProvider>
          </div>
        )}
      </div>

      <div className="flex h-10 flex-1 items-center justify-center">
        <TabsNavigation />
      </div>

      <nav className="w-[200px] flex items-center justify-end">
        <div className="flex items-center gap-2">
          {!isGuestMode && (
            <Button
              variant="ghost"
              size="icon"
              side="bottom"
              tooltip={`${t('header.logout')} (${username})`}
              onClick={handleLogout}
            >
              <LogOutIcon className="size-4" aria-hidden="true" />
            </Button>
          )}
        </div>
      </nav>
    </header>
  )
}
