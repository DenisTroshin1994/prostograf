// Вкладка «Чат» теперь работает поверх серверных диалогов (единая история).
// Вся логика — в переиспользуемом ChatPanel (он же используется на /chat).
import ChatPanel from '@/features/ChatPanel'

export default function RetrievalView() {
  return <ChatPanel />
}
