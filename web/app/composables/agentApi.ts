export const agentApi = <T>(path: string, options?: { method?: 'GET' | 'POST' | 'PUT' | 'DELETE'; body?: Record<string, unknown> }) =>
  $fetch<T>(`/api/agent${path}`, {
    ...options, headers: { 'x-zhihu-workbench': '1' }, retry: 0,
    onResponseError({ response }) {
      if (import.meta.client && response.status === 401 && !path.startsWith('/auth/')) {
        window.dispatchEvent(new Event('zhihu-session-check'))
      }
    },
  })

export function errorText(error: unknown) {
  const data = (error as { data?: { detail?: unknown; data?: { detail?: unknown } } } | null)?.data
  const detail = data?.detail ?? data?.data?.detail
  return typeof detail === 'string' ? detail : '请求失败。请检查后端是否运行，再重试。'
}

export function errorStatus(error: unknown) {
  return (error as { status?: number } | null)?.status
}

export const stages = [
  ['fetch', '获取候选'], ['plan', '制定计划'], ['execute', '阅读正文'],
  ['validate', '校验证据'], ['reflect', '逐条评审'], ['publish', '保存报告'],
] as const

export const statusLabels = {
  queued: '等待中', running: '运行中', completed: '已完成', failed: '执行失败', interrupted: '已中断',
} as const

export const formatTime = (value: string) => new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit',
}).format(new Date(value))

export function zhihuUrl(value: string | undefined) {
  if (!value) return undefined
  try {
    const url = new URL(value)
    if (url.protocol === 'https:' && ['www.zhihu.com', 'zhuanlan.zhihu.com'].includes(url.hostname)) return url.href
  } catch { /* No link for malformed data. */ }
  return undefined
}
