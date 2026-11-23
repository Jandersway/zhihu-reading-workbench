import { inject, shallowRef } from 'vue'
import type { InjectionKey } from 'vue'
import type { FeedSnapshot } from '~/types/discovery'

const STORAGE_KEY = 'zhihu-discovery-current-feed'

export function createDiscoveryFeed() {
  const snapshot = shallowRef<FeedSnapshot | null>(null)
  let account: string | null | undefined

  function setAccount(next: string | null) {
    if (next === account) return
    const restore = account === undefined && next !== null
    account = next
    snapshot.value = null
    try {
      if (restore) {
        const stored = JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? 'null')
        if (stored?.account === next && stored.snapshot?.result?.source === 'feed'
          && typeof stored.snapshot.result.batch_id === 'string' && Array.isArray(stored.snapshot.result.items)) {
          snapshot.value = stored.snapshot
          return
        }
      }
      sessionStorage.removeItem(STORAGE_KEY)
    } catch { /* The feed can still be retained in memory. */ }
  }

  function save(value: FeedSnapshot) {
    if (!account || value.result.source !== 'feed') return
    snapshot.value = value
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ account, snapshot: value }))
    } catch { /* The feed can still be retained in memory. */ }
  }

  return { snapshot, setAccount, save }
}

export const discoveryFeedKey: InjectionKey<ReturnType<typeof createDiscoveryFeed>> = Symbol('discovery-feed')

export function useDiscoveryFeed() {
  return inject(discoveryFeedKey)!
}
