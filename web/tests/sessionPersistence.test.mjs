import assert from 'node:assert/strict'
import { beforeEach, test } from 'node:test'
import { createDiscoveryFeed } from '../app/composables/useDiscoveryFeed.ts'
import { createReaderConversations } from '../app/composables/useReaderConversations.ts'

beforeEach(() => {
  const records = new Map()
  globalThis.sessionStorage = {
    getItem: key => records.get(key) ?? null,
    setItem: (key, value) => records.set(key, value),
    removeItem: key => records.delete(key),
  }
})

test('discovery retains the current order across reload and clears it on logout', () => {
  const batch = {
    result: {
      source: 'feed',
      batch_id: 'example-batch',
      items: [{ candidate_id: 'answer:2' }, { candidate_id: 'answer:1' }],
    },
    batchRatings: { 'answer:1': 2 },
  }
  const feed = createDiscoveryFeed()
  feed.setAccount('example-account')
  feed.save(batch)
  const reloaded = createDiscoveryFeed()
  reloaded.setAccount('example-account')
  assert.deepEqual(reloaded.snapshot.value, batch)
  reloaded.setAccount(null)
  assert.equal(reloaded.snapshot.value, null)
  const afterLogout = createDiscoveryFeed()
  afterLogout.setAccount('example-account')
  assert.equal(afterLogout.snapshot.value, null)
})

test('reader restores each completed turn and rejects a reply after account change', t => {
  const account = 'example-account'
  const article = 'answer-1'
  const response = { answer: 'Example answer', generation: { mode: 'live', provider: 'Example', model: 'demo' } }
  const reader = createReaderConversations()
  const reloaded = createReaderConversations()
  t.after(() => {
    reader.setAccount(null)
    reloaded.setAccount(null)
  })
  reader.setAccount(account)
  reader.finish(article, reader.begin(article), { question: 'First', result: response })
  reader.finish(article, reader.begin(article), { question: 'Second', result: response })
  reloaded.setAccount(account)
  const restored = reloaded.conversations.value.get(article).turns
  assert.deepEqual(restored.map(turn => turn.question), ['Second', 'First'])
  assert.deepEqual(restored[0].result, response)
  const pending = reloaded.begin(article)
  reloaded.setAccount('other-account')
  assert.equal(reloaded.finish(article, pending, { question: 'Delayed reply', result: response }), false)
  assert.equal(reloaded.conversations.value.size, 0)
})
