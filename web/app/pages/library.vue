<script setup lang="ts">
import { BookOpen, ChevronRight, Plus, Search, Trash2 } from 'lucide-vue-next'
import type { LibraryItem, LibrarySummary } from '~/types/agent'

const route = useRoute()
const mode = ref<'zhihu' | 'note'>('zhihu')
const url = ref(typeof route.query.url === 'string' ? route.query.url : '')
const title = ref('')
const body = ref('')
const filter = ref('')
const addPanel = ref<HTMLDetailsElement | null>(null)
const readerArea = ref<HTMLElement | null>(null)
const rows = ref<LibrarySummary[]>([])
const showAdd = computed(() => typeof route.query.url === 'string' || route.query.add === '1')
const selected = ref<LibraryItem | null>(null)
const loadingList = ref(true)
const loadingItem = ref(false)
const busy = ref(false)
const removing = ref(false)
const error = ref('')
const message = ref('')
const sourceNames = { zhihu: '知乎正文', note: '本地笔记', demo: '离线示例' }
const filteredRows = computed(() => {
  const term = filter.value.trim().toLocaleLowerCase()
  return rows.value.filter(row => `${row.title} ${row.author}`.toLocaleLowerCase().includes(term))
})
let selectionVersion = 0

function clearSelection() {
  selectionVersion++
  selected.value = null
  loadingItem.value = false
  removing.value = false
}

async function openItem(id: string) {
  clearSelection()
  const version = selectionVersion
  loadingItem.value = true
  error.value = ''
  try {
    const item = await agentApi<LibraryItem>(`/library/${id}`)
    if (version === selectionVersion) selected.value = item
  } catch (reason) {
    if (version === selectionVersion) error.value = errorText(reason)
  } finally {
    if (version === selectionVersion) loadingItem.value = false
  }
}

function followRoute(id: unknown) {
  if (typeof id === 'string' && /^(demo|(?:answer|article)-[0-9]{1,24}|note-[a-f0-9]{32})$/.test(id)) {
    if (selected.value?.id !== id) void openItem(id)
  } else clearSelection()
}

watch(() => route.query.item, followRoute)
watch(() => route.query.url, (value) => {
  if (typeof value === 'string') {
    mode.value = 'zhihu'
    url.value = value
    if (addPanel.value) addPanel.value.open = true
  }
})

onMounted(async () => {
  followRoute(route.query.item)
  try { rows.value = await agentApi<LibrarySummary[]>('/library') }
  catch (reason) { error.value = errorText(reason) }
  finally { loadingList.value = false }
})
onBeforeUnmount(() => { selectionVersion++ })

function keep(item: LibraryItem) {
  const { body: _body, ...summary } = item
  rows.value = [summary, ...rows.value.filter(row => row.id !== item.id)]
}

async function choose(id: string) {
  if (busy.value) return
  await navigateTo({ query: { item: id } })
  await nextTick()
  if (window.matchMedia('(max-width: 920px)').matches) readerArea.value?.scrollIntoView({ block: 'start' })
}

async function add(source: 'zhihu' | 'note') {
  if (busy.value || loadingList.value) return
  busy.value = true
  error.value = ''
  message.value = ''
  const data = source === 'zhihu' ? { source, url: url.value.trim() }
    : { source, title: title.value.trim(), body: body.value.trim() }
  try {
    const item = await agentApi<LibraryItem>('/library', { method: 'POST', body: data })
    keep(item)
    clearSelection()
    selected.value = item
    filter.value = ''
    message.value = '内容已保存到阅读库。'
    if (source === 'note') { title.value = ''; body.value = '' }
    if (source === 'zhihu') url.value = ''
    if (addPanel.value) addPanel.value.open = false
    await navigateTo({ query: { item: item.id } }, { replace: true })
  } catch (reason) { error.value = errorText(reason) }
  finally { busy.value = false }
}

async function remove() {
  if (!selected.value || busy.value) return
  busy.value = true
  error.value = ''
  message.value = ''
  const id = selected.value.id
  try {
    await agentApi(`/library/${id}`, { method: 'DELETE' })
    rows.value = rows.value.filter(row => row.id !== id)
    if (selected.value?.id === id) {
      clearSelection()
      await navigateTo({ query: {} }, { replace: true })
    }
    message.value = '已从阅读库移除。'
  } catch (reason) { error.value = errorText(reason) }
  finally { busy.value = false }
}
</script>

<template>
  <div class="page-stack">
    <header class="page-heading"><h1>我的阅读库</h1><p>在这里接着读，也可以对保存的原文提问。</p></header>
    <p v-if="error" role="alert" class="notice error">{{ error }}</p>
    <p v-else-if="message" role="status" class="notice">{{ message }}</p>
    <div class="library-layout">
      <section class="panel task-panel" aria-labelledby="saved-heading">
        <div class="library-list-title"><h2 id="saved-heading">已保存</h2><span v-if="!loadingList" class="caption">{{ rows.length }} 篇</span></div>
        <form v-if="rows.length" class="library-search" role="search" aria-label="在已保存的内容中查找" @submit.prevent>
          <Search :size="16" aria-hidden="true" />
          <label for="library-filter" class="sr-only">搜索阅读库</label>
          <input id="library-filter" v-model="filter" type="search" placeholder="筛选已保存的标题或作者" />
        </form>
        <details ref="addPanel" :open="showAdd" class="add-panel">
          <summary><Plus :size="16" />添加文章或笔记</summary>
          <div class="segmented mt-3" role="group" aria-label="添加方式"><button type="button" :aria-pressed="mode === 'zhihu'" @click="mode = 'zhihu'">知乎链接</button><button type="button" :aria-pressed="mode === 'note'" @click="mode = 'note'">手动粘贴</button></div>
          <form class="mt-3 grid gap-2" @submit.prevent="add(mode)">
            <template v-if="mode === 'zhihu'"><label for="article-url" class="field-label">回答或专栏链接</label><input id="article-url" v-model="url" type="url" required maxlength="2000" class="field min-w-0" placeholder="https://zhuanlan.zhihu.com/p/…" /></template>
            <template v-else><label for="library-title" class="field-label">标题</label><input id="library-title" v-model="title" required maxlength="300" class="field min-w-0" /><label for="library-body" class="field-label">正文</label><textarea id="library-body" v-model="body" required maxlength="300000" rows="5" class="field min-w-0" /></template>
            <button class="primary justify-self-start" :disabled="busy || loadingList">{{ busy ? '处理中…' : '加入阅读库' }}</button>
          </form>
        </details>
        <p v-if="loadingList" class="caption mt-3" role="status">正在读取…</p>
        <p v-else-if="!rows.length" class="caption mt-3">还没有保存内容。<NuxtLink to="/discover" class="text-link">去发现文章</NuxtLink></p>
        <div v-else-if="!filteredRows.length" class="caption mt-3"><p>没有找到匹配的内容。</p><button type="button" class="text-link" @click="filter = ''">清除筛选</button></div>
        <ul v-else class="library-list">
          <li v-for="row in filteredRows" :key="row.id">
            <button type="button" :aria-pressed="route.query.item === row.id" :disabled="busy" @click="choose(row.id)">
              <span><strong>{{ row.title }}</strong><small class="caption">{{ sourceNames[row.source] }} · {{ formatTime(row.updated_at) }}</small></span><ChevronRight :size="16" class="shrink-0" />
            </button>
          </li>
        </ul>
      </section>
      <section ref="readerArea" id="library-article" class="panel task-panel reader-area" aria-label="阅读区">
        <p v-if="loadingItem" role="status" class="caption">正在读取正文…</p>
        <ArticleReader v-else-if="selected" :item="selected" @saved="keep">
          <template #actions><button type="button" class="text-link" :disabled="busy" @click="removing = !removing"><Trash2 :size="15" />移除</button></template>
          <template #notice>
            <div v-if="removing" class="notice removal-prompt">
              <span>移除这篇文章的本地副本？</span><button type="button" class="secondary" :disabled="busy" @click="remove">确认移除</button><button type="button" class="text-link" :disabled="busy" @click="removing = false">取消</button>
            </div>
          </template>
        </ArticleReader>
        <div v-else class="empty-reader">
          <span class="tile-icon"><BookOpen :size="20" /></span><h2 class="mt-3">{{ rows.length ? '选择一篇文章开始阅读' : '保存后就能在这里阅读' }}</h2>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.library-layout { display: grid; grid-template-columns: minmax(237px, 32%) minmax(0, 1fr); align-items: start; gap: 16px; }
.library-list-title { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.library-search { display: flex; align-items: center; gap: 7px; margin-top: 12px; padding-inline: 9px; border: 1px solid var(--line); border-radius: 8px; background: var(--canvas); color: var(--muted); }
.library-search svg { flex: none; }
.library-search input { flex: 1; min-width: 0; min-height: 39px; outline: none; }
.library-search:focus-within { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-soft); }
.add-panel { margin-top: 12px; border-block: 1px solid var(--line); padding-block: 9px; }
.add-panel summary { display: flex; align-items: center; gap: 7px; color: var(--accent); font-weight: 690; }
.library-list { margin-top: 8px; }
.library-list button { display: flex; width: 100%; align-items: center; justify-content: space-between; gap: 8px; border-radius: 7px; padding: 10px 8px; text-align: left; overflow-wrap: anywhere; }
.library-list button:hover, .library-list button[aria-pressed='true'] { color: var(--accent); background: var(--accent-soft); }
.library-list small { display: block; margin-top: 4px; }
.reader-area { position: relative; scroll-margin-top: 10px; }
.removal-prompt { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 12px; }
.empty-reader { padding-block: clamp(24px, 7vw, 88px); text-align: center; color: var(--muted); }
@media (max-width: 920px) {
  .library-layout { grid-template-columns: minmax(0, 1fr); }
  .library-list { max-height: 235px; overflow: auto; }
}
</style>
