/** 统一请求封装：拼后端地址、带身份头、抛网络错误。 */
const API_BASE = import.meta.env.VITE_API_BASE ?? ''
export const ACTOR_STORAGE_KEY = 'data-insight-actor'

/** 演示身份：真实部署改为登录会话，前端不再自选账号。 */
export const ACTORS = [
  { id: 'borrower-gamma', name: '使用方 Gamma', role: '借阅者' },
  { id: 'owner-alpha', name: '数据集维护者 Alpha', role: '所有者' },
  { id: 'owner-beta', name: '数据集维护者 Beta', role: '所有者' },
  { id: 'platform-admin', name: '平台管理员', role: '管理员' },
  { id: '', name: '公开浏览者', role: '只读' },
]

export function currentActorId(): string {
  return window.localStorage.getItem(ACTOR_STORAGE_KEY) ?? 'borrower-gamma'
}

export function setCurrentActorId(actorId: string): void {
  window.localStorage.setItem(ACTOR_STORAGE_KEY, actorId)
}

function identityHeaders(): Record<string, string> {
  const actorId = currentActorId()
  return actorId ? { 'X-Actor-Id': actorId } : {}
}

export function request(path: string, init?: RequestInit): Promise<Response> {
  const url = path.startsWith('http') ? path : `${API_BASE}${path}`
  return fetch(url, {
    headers: { 'Content-Type': 'application/json', ...identityHeaders() },
    ...init,
  }).catch((error: unknown) => {
    const detail = error instanceof Error ? error.message : '请求未送达'
    throw new Error(`接口请求失败：${detail}`)
  })
}

export async function fetchJson<T>(path: string): Promise<T> {
  const response = await request(path)
  if (!response.ok) {
    throw new Error(`接口返回 ${response.status}，数据未更新`)
  }
  return (await response.json()) as T
}

export async function readError(response: Response, fallback: string): Promise<string> {
  try {
    const payload = await response.json()
    if (payload?.detail) return String(payload.detail)
    if (payload?.message) return String(payload.message)
  } catch {
    /* 响应不是 JSON 时退回固定文案 */
  }
  return fallback
}
