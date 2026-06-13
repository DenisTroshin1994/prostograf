import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import {
  AdminUser, DepartmentInfo,
  adminListUsers, adminCreateUser, adminUpdateUser, adminDeleteUser,
  adminListDepartments, adminCreateDepartment, adminRenameDepartment, adminDeleteDepartment,
  adminGetDeptAccess, adminSetDeptAccess, listAllDocFilePaths,
  adminGetDeptPrompt, adminSetDeptPrompt,
  adminGetBranding, adminSetBranding
} from '@/api/lightrag'
import { useAuthStore } from '@/stores/state'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Trash2Icon, PencilIcon, SaveIcon, ChevronLeftIcon, ChevronRightIcon } from 'lucide-react'

const USERS_PAGE_SIZE = 50

function Select({ value, onChange, children, disabled }: { value: string; onChange: (v: string) => void; children: React.ReactNode; disabled?: boolean }) {
  return (
    <select
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      className="h-9 rounded-md border border-input bg-background px-2 text-sm"
    >
      {children}
    </select>
  )
}

export default function AdminUsersView() {
  const [users, setUsers] = useState<AdminUser[]>([])
  const [usersTotal, setUsersTotal] = useState(0)
  const [usersPage, setUsersPage] = useState(1)
  const [usersSearch, setUsersSearch] = useState('')
  const [departments, setDepartments] = useState<DepartmentInfo[]>([])
  const [allDocs, setAllDocs] = useState<string[]>([])

  // Форма нового пользователя
  const [nu, setNu] = useState({ login: '', password: '', role: 'user', department: '', display_name: '' })
  const [newDept, setNewDept] = useState('')

  // Матрица доступа выбранного отдела
  const [accessDept, setAccessDept] = useState<string>('')
  const [accessSet, setAccessSet] = useState<Set<string>>(new Set())
  const [docFilter, setDocFilter] = useState('')
  const [deptPrompt, setDeptPrompt] = useState('')

  // Оформление (брендинг)
  const [brandName, setBrandName] = useState('')
  const [brandDesc, setBrandDesc] = useState('')

  const totalPages = Math.max(1, Math.ceil(usersTotal / USERS_PAGE_SIZE))

  const reloadUsers = useCallback(async () => {
    try {
      const res = await adminListUsers(usersPage, USERS_PAGE_SIZE, usersSearch)
      setUsers(res.items)
      setUsersTotal(res.total)
    } catch (e: any) {
      if (e?.message !== 'Authentication required') {
        toast.error('Не удалось загрузить пользователей')
      }
    }
  }, [usersPage, usersSearch])

  const reloadMeta = useCallback(async () => {
    try {
      const [d, docs, brand] = await Promise.all([
        adminListDepartments(), listAllDocFilePaths(), adminGetBranding()
      ])
      setDepartments(d)
      setAllDocs(docs)
      setBrandName(brand.app_name || '')
      setBrandDesc(brand.login_description || '')
    } catch (e: any) {
      // При 401 интерсептор сам разлогинивает и уводит на /login.
      if (e?.message !== 'Authentication required') {
        toast.error('Не удалось загрузить данные администратора')
      }
    }
  }, [])

  useEffect(() => { reloadMeta() }, [reloadMeta])
  useEffect(() => { reloadUsers() }, [reloadUsers])

  const deptNames = departments.map((d) => d.name)

  // ── Пользователи ──
  const createUser = async () => {
    if (!nu.login.trim() || !nu.password.trim()) {
      toast.error('Укажите логин и пароль')
      return
    }
    try {
      await adminCreateUser({ login: nu.login.trim(), password: nu.password, role: nu.role, department: nu.department, display_name: nu.display_name.trim() })
      setNu({ login: '', password: '', role: 'user', department: '', display_name: '' })
      await reloadUsers()
      toast.success('Пользователь создан')
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || 'Не удалось создать пользователя')
    }
  }

  const patchUser = async (login: string, patch: Partial<{ role: string; department: string; password: string; display_name: string }>) => {
    try {
      await adminUpdateUser(login, patch)
      await reloadUsers()
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || 'Не удалось изменить пользователя')
    }
  }

  const removeUser = async (login: string) => {
    if (!window.confirm(`Удалить пользователя «${login}»?`)) return
    try {
      await adminDeleteUser(login)
      await reloadUsers()
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || 'Не удалось удалить пользователя')
    }
  }

  // ── Отделы ──
  const createDept = async () => {
    if (!newDept.trim()) return
    try {
      await adminCreateDepartment(newDept.trim())
      setNewDept('')
      await reloadMeta()
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || 'Не удалось создать отдел')
    }
  }

  const renameDept = async (name: string) => {
    const nn = window.prompt(`Новое имя отдела «${name}»:`, name)
    if (!nn || nn.trim() === name) return
    try {
      await adminRenameDepartment(name, nn.trim())
      if (accessDept === name) setAccessDept(nn.trim())
      await reloadMeta()
      await reloadUsers()
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || 'Не удалось переименовать отдел')
    }
  }

  const removeDept = async (name: string) => {
    if (!window.confirm(`Удалить отдел «${name}»? Пользователи отдела останутся без отдела (без доступа).`)) return
    try {
      await adminDeleteDepartment(name)
      if (accessDept === name) { setAccessDept(''); setAccessSet(new Set()) }
      await reloadMeta()
      await reloadUsers()
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || 'Не удалось удалить отдел')
    }
  }

  // ── Доступ отдела к документам ──
  const selectAccessDept = async (name: string) => {
    setAccessDept(name)
    setAccessSet(new Set())
    setDeptPrompt('')
    if (!name) return
    try {
      const { files } = await adminGetDeptAccess(name)
      setAccessSet(new Set(files))
    } catch {
      toast.error('Не удалось загрузить доступ отдела')
    }
    try {
      const { prompt } = await adminGetDeptPrompt(name)
      setDeptPrompt(prompt || '')
    } catch { /* ignore */ }
  }

  const saveDeptPrompt = async () => {
    if (!accessDept) return
    try {
      await adminSetDeptPrompt(accessDept, deptPrompt)
      toast.success(`Промпт отдела «${accessDept}» сохранён`)
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || 'Не удалось сохранить промпт отдела')
    }
  }

  const toggleDoc = (fp: string) => {
    setAccessSet((prev) => {
      const next = new Set(prev)
      if (next.has(fp)) next.delete(fp)
      else next.add(fp)
      return next
    })
  }

  const saveAccess = async () => {
    if (!accessDept) return
    try {
      await adminSetDeptAccess(accessDept, Array.from(accessSet))
      await reloadMeta()
      toast.success(`Доступ отдела «${accessDept}» сохранён`)
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || 'Не удалось сохранить доступ')
    }
  }

  // ── Оформление (брендинг) ──
  const saveBranding = async () => {
    try {
      const res = await adminSetBranding({ app_name: brandName, login_description: brandDesc })
      setBrandName(res.app_name)
      setBrandDesc(res.login_description)
      // Применяем локально сразу — название/описание обновятся в шапке/входе.
      useAuthStore.getState().setBranding(res.app_name, res.login_description)
      toast.success('Оформление сохранено')
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || 'Не удалось сохранить оформление')
    }
  }

  const filteredDocs = allDocs.filter((d) => d.toLowerCase().includes(docFilter.toLowerCase()))

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6 overflow-auto p-6 pb-16">
      {/* Пользователи */}
      <Card>
        <CardHeader><CardTitle>Пользователи</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-4">
          {/* Новый пользователь */}
          <div className="flex flex-wrap items-end gap-2 rounded-md border border-dashed p-3">
            <div className="flex flex-col gap-1">
              <label className="text-xs text-muted-foreground">Логин</label>
              <Input value={nu.login} onChange={(e) => setNu({ ...nu, login: e.target.value })} className="h-9 w-36" />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs text-muted-foreground">Пароль</label>
              <Input type="password" value={nu.password} onChange={(e) => setNu({ ...nu, password: e.target.value })} className="h-9 w-36" />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs text-muted-foreground">Имя</label>
              <Input value={nu.display_name} onChange={(e) => setNu({ ...nu, display_name: e.target.value })} className="h-9 w-36" />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs text-muted-foreground">Роль</label>
              <Select value={nu.role} onChange={(v) => setNu({ ...nu, role: v })}>
                <option value="user">Пользователь</option>
                <option value="admin">Администратор</option>
              </Select>
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs text-muted-foreground">Отдел</label>
              <Select value={nu.department} onChange={(v) => setNu({ ...nu, department: v })}>
                <option value="">— нет —</option>
                {deptNames.map((d) => <option key={d} value={d}>{d}</option>)}
              </Select>
            </div>
            <Button onClick={createUser} size="sm">Добавить</Button>
          </div>

          {/* Поиск + счётчик */}
          <div className="flex flex-wrap items-center gap-2">
            <Input
              value={usersSearch}
              onChange={(e) => { setUsersSearch(e.target.value); setUsersPage(1) }}
              placeholder="Поиск по логину или имени…"
              className="h-9 w-64"
            />
            <span className="text-muted-foreground text-xs">Всего: {usersTotal}</span>
          </div>

          {/* Таблица пользователей */}
          <div className="overflow-auto">
            <table className="w-full text-sm">
              <thead className="text-muted-foreground text-left text-xs uppercase">
                <tr>
                  <th className="px-2 py-1">Логин</th>
                  <th className="px-2 py-1">Имя</th>
                  <th className="px-2 py-1">Роль</th>
                  <th className="px-2 py-1">Отдел</th>
                  <th className="px-2 py-1"></th>
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.login} className="border-t border-border/50">
                    <td className="px-2 py-1.5 font-medium">{u.login}</td>
                    <td className="px-2 py-1.5">{u.display_name}</td>
                    <td className="px-2 py-1.5">
                      <Select value={u.role} onChange={(v) => patchUser(u.login, { role: v })}>
                        <option value="user">Пользователь</option>
                        <option value="admin">Администратор</option>
                      </Select>
                    </td>
                    <td className="px-2 py-1.5">
                      <Select value={u.department} onChange={(v) => patchUser(u.login, { department: v })}>
                        <option value="">— нет —</option>
                        <option value="all">all (полный)</option>
                        {deptNames.map((d) => <option key={d} value={d}>{d}</option>)}
                      </Select>
                    </td>
                    <td className="px-2 py-1.5">
                      <div className="flex items-center gap-1">
                        <button
                          title="Сменить пароль"
                          className="rounded p-1 hover:bg-accent/40"
                          onClick={() => { const p = window.prompt(`Новый пароль для «${u.login}»:`); if (p && p.trim()) patchUser(u.login, { password: p.trim() }).then(() => toast.success('Пароль изменён')) }}
                        >
                          <PencilIcon className="size-4" />
                        </button>
                        <button title="Удалить" className="rounded p-1 hover:text-destructive" onClick={() => removeUser(u.login)}>
                          <Trash2Icon className="size-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
                {users.length === 0 && (
                  <tr><td colSpan={5} className="text-muted-foreground px-2 py-4 text-center">Пользователи не найдены.</td></tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Пагинация */}
          {totalPages > 1 && (
            <div className="flex items-center justify-center gap-3 text-sm">
              <Button variant="outline" size="sm" disabled={usersPage <= 1} onClick={() => setUsersPage((p) => Math.max(1, p - 1))}>
                <ChevronLeftIcon className="size-4" />
              </Button>
              <span className="text-muted-foreground">Стр. {usersPage} из {totalPages}</span>
              <Button variant="outline" size="sm" disabled={usersPage >= totalPages} onClick={() => setUsersPage((p) => Math.min(totalPages, p + 1))}>
                <ChevronRightIcon className="size-4" />
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Отделы */}
      <Card>
        <CardHeader><CardTitle>Отделы</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex items-end gap-2">
            <div className="flex flex-col gap-1">
              <label className="text-xs text-muted-foreground">Новый отдел</label>
              <Input value={newDept} onChange={(e) => setNewDept(e.target.value)} className="h-9 w-56" placeholder="Название отдела" />
            </div>
            <Button onClick={createDept} size="sm">Создать</Button>
          </div>
          <div className="flex flex-wrap gap-2">
            {departments.map((d) => (
              <div key={d.name} className="flex items-center gap-2 rounded-md border px-2 py-1 text-sm">
                <span className="font-medium">{d.name}</span>
                <span className="text-muted-foreground text-xs">{d.doc_count} док.</span>
                <button title="Переименовать" className="hover:text-primary" onClick={() => renameDept(d.name)}><PencilIcon className="size-3.5" /></button>
                <button title="Удалить" className="hover:text-destructive" onClick={() => removeDept(d.name)}><Trash2Icon className="size-3.5" /></button>
              </div>
            ))}
            {departments.length === 0 && <span className="text-muted-foreground text-sm">Отделов пока нет.</span>}
          </div>
        </CardContent>
      </Card>

      {/* Доступ отдела к документам */}
      <Card>
        <CardHeader><CardTitle>Доступ отдела к документам</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-3">
          <p className="text-muted-foreground text-sm">
            Отметьте документы, доступные отделу. Неотмеченные документы (и их узлы графа) физически не
            попадут в ответы пользователям этого отдела. Новые документы по умолчанию недоступны.
          </p>
          <div className="flex items-center gap-2">
            <Select value={accessDept} onChange={selectAccessDept}>
              <option value="">— выберите отдел —</option>
              {deptNames.map((d) => <option key={d} value={d}>{d}</option>)}
            </Select>
            {accessDept && (
              <>
                <Input value={docFilter} onChange={(e) => setDocFilter(e.target.value)} placeholder="Фильтр документов…" className="h-9 w-64" />
                <Button
                  variant="outline"
                  size="sm"
                  title={docFilter ? 'Выбрать все отфильтрованные' : 'Выбрать все документы'}
                  onClick={() => setAccessSet((prev) => new Set([...prev, ...filteredDocs]))}
                >
                  Выбрать все{docFilter ? ` (${filteredDocs.length})` : ''}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  title={docFilter ? 'Снять отметки с отфильтрованных' : 'Снять все отметки'}
                  onClick={() => setAccessSet((prev) => {
                    const next = new Set(prev)
                    filteredDocs.forEach((d) => next.delete(d))
                    return next
                  })}
                >
                  Снять все{docFilter ? ` (${filteredDocs.length})` : ''}
                </Button>
                <span className="text-muted-foreground text-xs">{accessSet.size} из {allDocs.length} выбрано</span>
                <Button onClick={saveAccess} size="sm"><SaveIcon className="size-4" /> Сохранить</Button>
              </>
            )}
          </div>
          {accessDept && (
            <div className="max-h-96 overflow-auto rounded-md border p-2">
              {filteredDocs.map((fp) => (
                <label key={fp} className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-sm hover:bg-accent/30">
                  <input type="checkbox" checked={accessSet.has(fp)} onChange={() => toggleDoc(fp)} />
                  <span className="truncate">{fp}</span>
                </label>
              ))}
              {filteredDocs.length === 0 && <div className="text-muted-foreground p-2 text-sm">Документы не найдены.</div>}
            </div>
          )}

          {accessDept && (
            <div className="mt-2 flex flex-col gap-2 border-t border-border/60 pt-4">
              <label className="text-sm font-medium">Промпт ответа отдела «{accessDept}»</label>
              <p className="text-muted-foreground text-xs">
                Задаёт роль, тон и формат ответов для пользователей этого отдела (разработчикам — свой,
                менеджерам — свой). Применяется на сервере ко всем чатам отдела. Пусто — поведение по умолчанию.
              </p>
              <textarea
                value={deptPrompt}
                onChange={(e) => setDeptPrompt(e.target.value)}
                placeholder="Например: «Отвечай как технический специалист, кратко и со ссылками на разделы инструкций»"
                className="min-h-28 w-full resize-y rounded-md border border-input bg-background p-2 text-sm"
              />
              <div>
                <Button onClick={saveDeptPrompt} size="sm"><SaveIcon className="size-4" /> Сохранить промпт</Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Оформление (страница входа) */}
      <Card>
        <CardHeader><CardTitle>Оформление (страница входа и шапка)</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-3">
          <p className="text-muted-foreground text-sm">
            Название приложения и описание на странице входа. Применяется сразу для всех пользователей,
            пересборка и перезапуск не нужны. Пусто — значения по умолчанию.
          </p>
          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium">Название</label>
            <Input value={brandName} onChange={(e) => setBrandName(e.target.value)} maxLength={80} placeholder="ПростоГраф" className="h-9 w-72" />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium">Описание (подпись под названием на входе)</label>
            <textarea
              value={brandDesc}
              onChange={(e) => setBrandDesc(e.target.value)}
              maxLength={300}
              placeholder="Пожалуйста, введите ваш аккаунт и пароль для входа в систему"
              className="min-h-20 w-full resize-y rounded-md border border-input bg-background p-2 text-sm"
            />
          </div>
          <div>
            <Button onClick={saveBranding} size="sm"><SaveIcon className="size-4" /> Сохранить оформление</Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
