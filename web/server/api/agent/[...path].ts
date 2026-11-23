import { createError, getRequestHeader, getRequestURL, proxyRequest } from 'h3'

export default defineEventHandler((event) => {
  const method = event.method.toUpperCase()
  const suffix = event.path.slice('/api/agent'.length)
  const path = suffix.split('?')[0] ?? ''
  const routes: Record<string, RegExp> = {
    GET: /^\/(health|auth\/session|settings\/model|discover\/(?:topics|articles)|tasks(?:\/[a-f0-9]{32})?|lab\/(?:metrics|agent-rl|cases(?:\/[a-f0-9]{64}(?:\/comparison)?)?)|library(?:\/(?:questions|(?:preview|demo|(?:answer|article)-[0-9]{1,24}|note-[a-f0-9]{32})(?:\/comments)?))?)$/,
    POST: /^\/(tasks|auth\/(?:login|logout)|discover\/(?:collections\/sync|batches\/[a-f0-9]{32}\/rerank|evaluations\/[a-f0-9]{32})|library(?:\/(?:preview|demo|(?:answer|article)-[0-9]{1,24}|note-[a-f0-9]{32})\/ask)?)$/,
    PUT: /^\/(settings\/model|lab\/cases\/[a-f0-9]{64}\/ratings|discover\/(?:rating|visibility|evaluations\/[a-f0-9]{32}\/ratings))$/,
    DELETE: /^\/(settings\/model|library\/(demo|(?:answer|article)-[0-9]{1,24}|note-[a-f0-9]{32}))$/,
  }
  if (!routes[method]?.test(path)) throw createError({ statusCode: 404 })
  const origin = getRequestHeader(event, 'origin')
  if (origin && origin !== getRequestURL(event).origin) {
    throw createError({ statusCode: 403, statusMessage: 'Cross-origin request rejected' })
  }
  if (method !== 'GET' && getRequestHeader(event, 'x-zhihu-workbench') !== '1') {
    throw createError({ statusCode: 403, statusMessage: 'Missing workbench header' })
  }
  const api = useRuntimeConfig(event).agentApi
  return proxyRequest(event, `${api}${suffix}`)
})
