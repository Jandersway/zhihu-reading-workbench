<script setup lang="ts">
import { ArrowRight, BookOpen, ChevronDown, ChevronRight, Compass, LibraryBig, MessageCircleQuestion, Plus } from 'lucide-vue-next'
import type { LibrarySummary, Mode, ModelStatus, Task } from '~/types/agent'

const route = useRoute()
const keyword = ref('')
const saved = ref<LibrarySummary[] | null>(null)
const libraryError = ref('')
const libraryItems = computed(() => saved.value?.filter(item => item.source !== 'demo') ?? [])
const articleId = computed(() => typeof route.query.article === 'string'
  && /^(demo|(?:answer|article)-[0-9]{1,24}|note-[a-f0-9]{32})$/.test(route.query.article) ? route.query.article : null)
const runId = computed(() => typeof route.query.run === 'string'
  && /^[a-f0-9]{32}$/.test(route.query.run) ? route.query.run : '')
const showAsk = computed(() => !runId.value && (Boolean(articleId.value) || route.query.task === 'ask'))
const showHome = computed(() => !runId.value && !showAsk.value)
const question = ref('')
const libraryItemId = ref<string | null>(articleId.value)
const libraryItem = computed(() => saved.value?.find(item => item.id === libraryItemId.value))
const missingItem = computed(() => Boolean(libraryItemId.value && saved.value && !libraryItem.value))
const canAsk = computed(() => !!saved.value && (libraryItems.value.length > 0 || Boolean(libraryItem.value)))
const { suggestions, suggesting } = useQuestionSuggestions(() => {
  if (!showAsk.value || !canAsk.value || missingItem.value) return null
  return libraryItemId.value ? { item_id: libraryItemId.value } : {}
})
const scopeMenu = ref<HTMLDetailsElement | null>(null)
const mode = ref<Mode>('demo')
const consent = ref(false)
watch([mode, question, libraryItemId], () => { consent.value = false })
const modelStatus = ref<ModelStatus | null>(null)
const liveEnabled = computed(() => modelStatus.value?.live_enabled === true)
const modelProvider = computed(() => modelStatus.value?.providers.find(option => option.id === modelStatus.value?.provider))
const busy = ref(false)
const restoring = ref(Boolean(runId.value))
const error = ref('')
const task = ref<Task | null>(null)
const taskId = ref('')
let timer: ReturnType<typeof setTimeout> | undefined
let disposed = false
let pending: { nonce: string; focus: string; mode: Mode; kind: 'research'; consent: boolean;
  research_scope: 'library'; library_item_id: string | null } | undefined

async function refresh(id: string) {
  if (disposed || id !== taskId.value) return
  if (timer) clearTimeout(timer)
  try {
    const current = await agentApi<Task>(`/tasks/${id}`)
    if (disposed || id !== taskId.value) return
    task.value = current
    error.value = ''
    if (current.status === 'queued' || current.status === 'running') timer = setTimeout(() => refresh(id), 950)
  } catch (reason) {
    if (!disposed && id === taskId.value) error.value = errorText(reason)
  } finally {
    if (!disposed && id === taskId.value) restoring.value = false
  }
}

function syncRoute() {
  const id = runId.value
  if (id && id === taskId.value) return
  if (timer) clearTimeout(timer)
  task.value = null
  taskId.value = id
  restoring.value = Boolean(id)
  error.value = ''
  if (id) {
    void refresh(id)
    return
  }
  if (route.query.task === 'recommend') {
    void navigateTo('/discover', { replace: true })
    return
  }
  question.value = ''
  mode.value = 'demo'
  libraryItemId.value = articleId.value
}

watch(() => [route.query.run, route.query.task, route.query.article], syncRoute)

onMounted(() => {
  syncRoute()
  agentApi<ModelStatus>('/settings/model').then(data => { modelStatus.value = data }).catch(() => {})
  agentApi<LibrarySummary[]>('/library').then(data => { saved.value = data }).catch(reason => { libraryError.value = errorText(reason) })
})

onBeforeUnmount(() => {
  disposed = true
  if (timer) clearTimeout(timer)
})

function selectScope(id: string | null) {
  libraryItemId.value = id
  question.value = ''
  if (scopeMenu.value) {
    scopeMenu.value.open = false
    scopeMenu.value.querySelector('summary')?.focus()
  }
}

async function start() {
  if (!question.value.trim() || busy.value || !canAsk.value || missingItem.value
    || (mode.value === 'live' && (!liveEnabled.value || !consent.value))) return
  busy.value = true
  error.value = ''
  const location = route.fullPath
  const focus = question.value.trim()
  const itemId = libraryItemId.value
  if (!pending || pending.focus !== focus || pending.mode !== mode.value || pending.consent !== consent.value
    || pending.library_item_id !== itemId) {
    pending = { nonce: crypto.randomUUID(), focus, mode: mode.value, kind: 'research', consent: consent.value,
      research_scope: 'library', library_item_id: itemId }
  }
  try {
    const data = await agentApi<{ id: string }>('/tasks', { method: 'POST', body: pending })
    pending = undefined
    if (disposed || location !== route.fullPath) return
    taskId.value = data.id
    restoring.value = true
    consent.value = false
    await navigateTo({ query: { run: data.id, task: 'ask' } }, { replace: true })
    await refresh(data.id)
  } catch (reason) { error.value = errorText(reason) }
  finally { busy.value = false }
}

function findArticles(q: string) {
  return navigateTo({ path: '/discover', query: { q } })
}
</script>

<template>
  <div class="page-stack workbench">
    <section v-if="showHome" class="workbench-search" aria-labelledby="workbench-title">
      <div class="workbench-search-inner">
        <h1 id="workbench-title">今天想读什么？</h1>
        <p>搜一个主题，找到值得读的知乎文章。</p>
        <ArticleSearchBar v-model="keyword" @search="findArticles" />
        <NuxtLink to="/discover" class="feed-link"><Compass :size="16" /> 看为你推荐 <ArrowRight :size="16" /></NuxtLink>
      </div>
    </section>


    <section v-if="showAsk" class="page-stack ask-page" aria-labelledby="ask-title">
      <header class="page-heading"><h1 id="ask-title">资料问答</h1><p>从阅读库找答案，点引用回到原文。</p></header>
      <p v-if="libraryError" class="notice error" role="alert">{{ libraryError }}</p>
      <p v-else-if="saved === null" class="panel task-panel caption" role="status">正在读取阅读库…</p>
      <div v-else-if="!canAsk" class="panel ask-empty">
        <h2>先保存一篇你想读的文章</h2>
        <p class="caption">保存后，工作台会根据资料内容推荐可以问的问题。</p>
        <NuxtLink to="/discover" class="primary">发现文章 <ArrowRight :size="16" /></NuxtLink>
        <NuxtLink to="/library?add=1" class="secondary">添加资料</NuxtLink>
      </div>
      <div v-else class="panel ask-panel">
        <p v-if="missingItem" class="notice error mb-3" role="status">这篇资料已移除，请重新选择查找范围。</p>
        <QuestionComposer v-model="question" :suggestions="suggestions" :suggesting="suggesting"
          :busy="busy" :blocked="missingItem || (mode === 'live' && !consent)" @submit="start">
          <template #options>
            <details ref="scopeMenu" class="scope-menu">
              <summary aria-label="查找范围"><LibraryBig :size="15" /><span>{{ libraryItem?.title || '整个阅读库' }}</span><ChevronDown :size="14" /></summary>
              <div class="scope-options">
                <button type="button" :aria-pressed="libraryItemId === null" @click="selectScope(null)">整个阅读库</button>
                <button v-for="item in libraryItems" :key="item.id" type="button" :aria-pressed="libraryItemId === item.id" @click="selectScope(item.id)">{{ item.title }}</button>
              </div>
            </details>
            <div v-if="liveEnabled" class="segmented" role="group" aria-label="回答模式">
              <button type="button" :aria-pressed="mode === 'demo'" @click="mode = 'demo'">本地摘录</button>
              <button type="button" :aria-pressed="mode === 'live'" @click="mode = 'live'">实时模型</button>
            </div>
            <template v-else>
              <span class="caption">本地摘录</span>
              <NuxtLink v-if="modelStatus?.live_allowed" to="/settings" class="text-link">配置模型 API Key</NuxtLink>
            </template>
          </template>
          <label v-if="mode === 'live'" class="caption flex items-start gap-2">
            <input v-model="consent" type="checkbox" class="mt-1" />
            我同意本次将问题和检索到的资料发送到 {{ modelProvider?.host }}。调用可能计费。
          </label>
          <p v-if="error" class="notice error" role="alert">{{ error }}</p>
        </QuestionComposer>
      </div>
    </section>

    <section v-if="runId" class="page-stack" aria-labelledby="run-title">
      <header class="page-heading workbench-detail-heading">
        <NuxtLink to="/history" class="text-link">返回运行记录</NuxtLink>
        <h1 id="run-title">{{ task?.kind === 'recommend' ? '推荐报告' : '资料问答' }}</h1>
        <NuxtLink v-if="task?.kind === 'research'" :to="{ query: { task: 'ask', article: task.library_item_id ?? undefined } }" class="text-link mt-2">提出新问题 <ArrowRight :size="15" /></NuxtLink>
      </header>
      <p v-if="restoring" class="panel task-panel" role="status">正在读取运行记录…</p>
      <p v-if="error" class="notice error" role="alert">{{ error }} <button type="button" class="text-link" @click="refresh(runId)">重试</button></p>
      <TaskResult v-if="task?.kind === 'recommend'" :task="task" />
      <ResearchResult v-else-if="task?.kind === 'research'" :task="task" />
    </section>

    <div v-if="showHome" class="home-grid">
      <section class="panel task-panel" aria-labelledby="recent-library">
        <div class="home-card-heading">
          <span class="tile-icon"><BookOpen :size="19" /></span><h2 id="recent-library">最近保存</h2>
          <NuxtLink to="/library" class="text-link">全部文章 <ChevronRight :size="15" /></NuxtLink>
        </div>
        <p v-if="libraryError" class="caption mt-4" role="alert">{{ libraryError }}</p>
        <p v-else-if="saved === null" class="caption mt-4" role="status">正在读取阅读库…</p>
        <div v-else-if="!saved.length" class="home-empty">
          <p>发现值得回看的内容时，可以保存到阅读库。</p>
          <NuxtLink to="/library?add=1" class="text-link"><Plus :size="16" />添加第一篇文章</NuxtLink>
        </div>
        <ul v-else class="saved-list">
          <li v-for="item in saved.slice(0, 4)" :key="item.id">
            <NuxtLink :to="{ path: '/library', query: { item: item.id } }">
              <span><strong>{{ item.title }}</strong><small class="caption">{{ formatTime(item.updated_at) }}</small></span>
              <ChevronRight :size="16" />
            </NuxtLink>
          </li>
        </ul>
      </section>
      <section class="panel task-panel home-ask" aria-labelledby="home-ask">
        <span class="tile-icon"><MessageCircleQuestion :size="20" /></span>
        <h2 id="home-ask">问我的阅读库</h2>
        <p>从已保存的资料里找答案，每段引用都能回到原文。</p>
        <NuxtLink :to="{ query: { task: 'ask' } }" class="secondary">打开资料问答 <ArrowRight :size="16" /></NuxtLink>
      </section>
    </div>


  </div>
</template>

<style scoped>
.workbench-search {
  padding: clamp(23px, 5.1vw, 67px);
  border: 1px solid #d2e1d3;
  border-radius: 20px;
  background: linear-gradient(125deg, #f7faef 0%, #edf5e9 52%, #e0eddf 100%);
}
.workbench-search-inner { max-width: 660px; }
.workbench-search h1 { font-size: clamp(32px, 5vw, 58px); font-weight: 780; line-height: 1.15; letter-spacing: -.04em; }
.workbench-search p { margin-block: 12px 23px; color: var(--muted); font-size: 15px; line-height: 1.6; }
.feed-link { display: inline-flex; align-items: center; gap: 8px; margin-top: 13px; min-height: 36px; color: var(--accent); font-weight: 690; }
.feed-link:hover { text-decoration: underline; }
.home-grid { display: grid; gap: 18px; grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr); }
.home-card-heading { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.home-card-heading a { margin-left: auto; }
.home-empty, .home-ask p { color: var(--muted); line-height: 1.65; }
.home-empty { padding-block: 22px 6px; }
.home-empty a { margin-top: 10px; }
.home-ask h2 { margin-top: 8px; }
.home-ask p { margin: 6px 0 15px; }
.workbench-detail-heading { padding-top: 0; }
.saved-list { margin-top: 9px; }
.saved-list li + li { border-top: 1px solid var(--line); }
.saved-list a { display: flex; justify-content: space-between; gap: 10px; align-items: center; padding-block: 11px; }
.saved-list a:hover { color: var(--accent); }
.saved-list strong { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; overflow-wrap: anywhere; }
.saved-list small { display: block; margin-top: 2px; }
.saved-list svg { flex: none; color: var(--muted); }
@media (max-width: 820px) { .home-grid { grid-template-columns: minmax(0, 1fr); } }
.ask-page { width: min(100%, 760px); }
.ask-panel { padding: clamp(15px, 3vw, 23px); }
.ask-empty { padding: clamp(18px, 5vw, 38px); }
.ask-empty .caption { margin-block: 5px 15px; }
.ask-empty .secondary { margin-left: 7px; }
.scope-menu { position: relative; min-width: 0; }
.scope-menu > summary { list-style: none; display: flex; gap: 5px; align-items: center; max-width: min(51vw, 260px); min-height: 35px; padding-inline: 8px; color: var(--accent); border-radius: 7px; }
.scope-menu > summary::-webkit-details-marker { display: none; }
.scope-menu > summary:hover { background: var(--wash); }
.scope-menu > summary > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.scope-menu svg { flex: none; }
.scope-options { position: absolute; z-index: 3; inset: auto auto calc(100% + 4px) 0; width: min(300px, 68vw); max-height: 215px; overflow: auto; padding: 5px; border: 1px solid var(--line); border-radius: 10px; background: white; box-shadow: 0 6px 18px #172f2522; }
.scope-options button { display: block; width: 100%; padding: 7px 9px; border-radius: 6px; text-align: left; overflow-wrap: anywhere; }
.scope-options button:hover, .scope-options button[aria-pressed='true'] { background: var(--accent-soft); color: var(--accent); }
</style>
