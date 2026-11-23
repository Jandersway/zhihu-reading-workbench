const CURL_START = /^curl(?:\.exe)?(?=\s)/i
const ZHIHU_HOSTS = new Set(['www.zhihu.com', 'zhihu.com', 'zhuanlan.zhihu.com', 'api.zhihu.com'])
const COOKIE_OPTIONS = new Set(['-b', '--cookie'])
const HEADER_OPTIONS = new Set(['-H', '--header'])
const OTHER_OPTIONS = new Set(['-X', '--request', '-A', '--user-agent', '--data-raw'])

export function isCurlCopy(text: string) {
  return CURL_START.test(text.trimStart())
}

function cmdArguments(text: string) {
  // Single newlines after a caret continue the command. A doubled newline belongs to the argument.
  const command = text.trim().replace(/\^[ \t]*\r?\n(?!\r?\n)/g, ' ')
  // Chromium has used both ^\^" and ^"^" for quotes inside arguments.
  const words = /[ \t]+|\^"((?:(?:\^\\|\\)\^"|\^"\^"|\^\r?\n\r?\n|\^[^"\r\n]|[^"^\r\n])*)\^"|"((?:(?:\^\\|\\)\^"|\\"|\^[^"\r\n]|[^"^\r\n])*)"|[^\s^"&|<>;]+/gy
  const values: string[] = []
  let end = 0
  for (const match of command.matchAll(words)) {
    end = match.index + match[0].length
    if (!match[0].trim()) continue
    const quoted = match[1] ?? match[2]
    const value = quoted?.replace(/(?:\^\\|\\)\^"|\^"\^"|\\"|\^(?:\r?\n\r?\n|(.))/g,
      (escape, character: string | undefined) => character ?? (escape.endsWith('\n') ? '\n' : '"'))
      .replace(/\\\\/g, '\\')
    values.push(value ?? match[0])
    if (end < command.length && !/[ \t]/.test(command[end]!)) return null
  }
  return end === command.length ? values : null
}

export function cookieFromCurlCopy(text: string) {
  const args = cmdArguments(text)
  if (!args) return { error: '未识别这条 cURL 的格式。也可以直接粘贴「请求标头」中 Cookie 的值。' } as const
  let url = ''
  const cookies: string[] = []
  for (let index = 1; index < args.length; index++) {
    const arg = args[index]!
    if (arg === '--url') url = args[++index] ?? ''
    else if (arg.startsWith('https://') && !url) url = arg
    else if (COOKIE_OPTIONS.has(arg)) cookies.push(args[++index] ?? '')
    else if (HEADER_OPTIONS.has(arg)) {
      const header = args[++index] ?? ''
      const cookie = /^cookie:\s*(.+)$/i.exec(header)
      if (cookie?.[1]) cookies.push(cookie[1])
    } else if (OTHER_OPTIONS.has(arg)) index++
  }
  try {
    const target = new URL(url)
    if (target.protocol !== 'https:' || !ZHIHU_HOSTS.has(target.host)) throw new Error()
  } catch {
    return { error: '请从发往 www.zhihu.com 的请求复制 cURL（cmd）。' } as const
  }
  const cookie = cookies.join('; ').trim()
  if (!cookie) return { error: '这条请求没有携带 Cookie。请确认知乎已登录，刷新后换一条请求。' } as const
  return { cookie } as const
}
