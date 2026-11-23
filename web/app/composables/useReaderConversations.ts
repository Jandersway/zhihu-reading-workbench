import { computed, inject, reactive, shallowRef } from 'vue'
import type { InjectionKey } from 'vue'
import type { ResearchResult } from '~/types/agent'

interface ReaderTurn {
  id: string
  question: string
  result: ResearchResult
}

interface Conversation {
  expiresAt: number
  turns: ReaderTurn[]
}

const STORAGE_KEY = 'zhihu-reader-conversations'
const RETENTION_MS = 24 * 60 * 60 * 1000
const MAX_ARTICLES = 20
const MAX_TURNS = 20

export function createReaderConversations() {
  const conversations = shallowRef(new Map<string, Conversation>())
  const drafts = reactive(new Map<string, string>())
  const pending = reactive(new Map<string, symbol>())
  const browserSaved = shallowRef(true)
  let account: string | null | undefined
  let expiryTimer: ReturnType<typeof setTimeout> | undefined

  function persist() {
    if (expiryTimer !== undefined) clearTimeout(expiryTimer)
    expiryTimer = undefined
    const records = [...conversations.value]
    if (records.length) {
      const expiry = Math.min(...records.map(([, entry]) => entry.expiresAt))
      expiryTimer = setTimeout(prune, Math.max(0, expiry - Date.now()))
    }
    try {
      if (account && records.length) sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ account, records }))
      else sessionStorage.removeItem(STORAGE_KEY)
      browserSaved.value = true
    } catch {
      // Browser storage can be restricted or full. The running tab still has the results in memory.
      browserSaved.value = false
    }
  }

  function prune() {
    const now = Date.now()
    conversations.value = new Map([...conversations.value].filter(([, entry]) => entry.expiresAt > now))
    persist()
  }

  function setAccount(next: string | null) {
    if (account === next) return
    const shouldRestore = account === undefined && next !== null
    account = next
    drafts.clear()
    pending.clear()
    conversations.value = new Map()
    if (shouldRestore) {
      try {
        const previous = JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? 'null')
        if (previous?.account === next && Array.isArray(previous.records)) {
          conversations.value = new Map(previous.records.filter((record: unknown) => {
            if (!Array.isArray(record)) return false
            const [id, entry] = record
            return typeof id === 'string' && Number.isFinite(entry?.expiresAt) && Array.isArray(entry?.turns)
          }))
        }
      } catch { /* A missing or invalid snapshot starts an empty conversation. */ }
    }
    prune()
  }

  function begin(id: string) {
    if (!account || pending.has(id)) return
    const token = Symbol()
    pending.set(id, token)
    return token
  }

  function finish(id: string, token: symbol, turn?: Omit<ReaderTurn, 'id'>) {
    if (pending.get(id) !== token) return false
    pending.delete(id)
    if (!turn) return true
    const now = Date.now()
    const next = new Map(conversations.value)
    const existing = next.get(id)
    const turns = existing && existing.expiresAt > now ? existing.turns : []
    next.delete(id)
    next.set(id, { expiresAt: now + RETENTION_MS, turns: [{ id: crypto.randomUUID(), ...turn }, ...turns].slice(0, MAX_TURNS) })
    conversations.value = new Map([...next].slice(-MAX_ARTICLES))
    if (drafts.get(id)?.trim() === turn.question) drafts.delete(id)
    prune()
    return true
  }

  function clear(id: string) {
    const next = new Map(conversations.value)
    next.delete(id)
    conversations.value = next
    drafts.delete(id)
    persist()
  }

  return { conversations, drafts, pending, browserSaved, setAccount, begin, finish, clear }
}

export const readerConversationsKey: InjectionKey<ReturnType<typeof createReaderConversations>> = Symbol('reader-conversations')

export function useReaderConversations(articleId: () => string | undefined) {
  const storage = inject(readerConversationsKey)!
  return {
    ...storage,
    turns: computed(() => storage.conversations.value.get(articleId() ?? '')?.turns ?? []),
    asking: computed(() => storage.pending.has(articleId() ?? '')),
    question: computed({
      get: () => storage.drafts.get(articleId() ?? '') ?? '',
      set: (text: string) => {
        const id = articleId()
        if (id) storage.drafts.set(id, text)
      },
    }),
  }
}
