export type CommentLink =
  | { kind: 'search'; query: string }
  | { kind: 'article' | 'image' | 'external'; url: string }

export function commentLink(href: string): CommentLink | undefined {
  let url: URL
  try {
    url = new URL(href, 'https://www.zhihu.com')
    if (url.origin === 'https://link.zhihu.com' && url.pathname === '/') {
      url = new URL(url.searchParams.get('target') ?? '')
    }
  } catch { return }
  if (url.username || url.password) return

  const webZhihu = ['https://www.zhihu.com', 'https://zhihu.com'].includes(url.origin)
  if ((url.protocol === 'zhihu:' && url.host === 'search' && ['', '/'].includes(url.pathname))
      || (webZhihu && /^\/search\/?$/.test(url.pathname))) {
    const query = url.searchParams.get('q')?.trim()
    if (query) return { kind: 'search', query: query.slice(0, 100) }
  }

  const appItem = url.protocol === 'zhihu:' && /^(answers?|articles?)$/.test(url.host)
    ? /^\/([0-9]{1,24})\/?$/.exec(url.pathname) : null
  if (appItem) {
    const base = url.host.startsWith('answer') ? 'https://www.zhihu.com/answer' : 'https://zhuanlan.zhihu.com/p'
    return { kind: 'article', url: `${base}/${appItem[1]}` }
  }
  if (webZhihu && /^(\/question\/[0-9]{1,24})?\/answer\/[0-9]{1,24}\/?$/.test(url.pathname)) {
    return { kind: 'article', url: `https://www.zhihu.com${url.pathname}` }
  }
  if (url.origin === 'https://zhuanlan.zhihu.com' && /^\/p\/[0-9]{1,24}\/?$/.test(url.pathname)) {
    return { kind: 'article', url: `${url.origin}${url.pathname}` }
  }
  if (url.protocol === 'https:' && /^pic[a-z0-9]*\.zhimg\.com$/.test(url.host)) {
    return { kind: 'image', url: url.href }
  }
  if (['https:', 'http:'].includes(url.protocol)) return { kind: 'external', url: url.href }
}
