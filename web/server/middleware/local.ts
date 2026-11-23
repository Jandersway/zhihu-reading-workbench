import { createError, getRequestHost } from 'h3'

export default defineEventHandler((event) => {
  const host = getRequestHost(event)
  if (!/^(localhost|127\.0\.0\.1)(:\d+)?$/i.test(host)) {
    throw createError({ statusCode: 403, statusMessage: 'Local access only' })
  }
})
