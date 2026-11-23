<script setup lang="ts">
import { ArrowLeft, Bookmark, BookOpen, Check, ExternalLink, MessageCircleQuestion, MessageSquare } from 'lucide-vue-next'
import type { LibraryComment, LibraryItem, ModelStatus, ReaderTarget, ReadingPreview, ResearchResult } from '~/types/agent'
import { useReaderConversations } from '~/composables/useReaderConversations'

const props = defineProps<{ item?: LibraryItem; target?: ReaderTarget }>()
const emit = defineEmits<{ saved: [item: LibraryItem] }>()
type Tab = 'body' | 'comments' | 'ask'
interface Page { item: LibraryItem; saved: boolean; tab: Tab; previewId?: string | null }

const current = ref<Page | null>(null)
const trail = ref<Page[]>([])
const loading = ref(false)
const loadError = ref('')
const comments = ref<LibraryComment[] | null>(null)
const loadingComments = ref(false)
const commentError = ref('')
const openingLink = ref(false)
const saving = ref(false)
const saveError = ref('')
const expired = ref(false)
const { question, asking, turns, browserSaved, begin, finish, clear } = useReaderConversations(() => current.value?.item.id)
const { suggestions, suggesting } = useQuestionSuggestions(() => {
  const page = current.value
  if (page?.tab !== 'ask' || expired.value) return null
  if (page.saved) return { item_id: page.item.id }
  return page.previewId ? { preview_id: page.previewId } : null
})
const readModel = ref<ModelStatus | null>(null)
const loadingModel = ref(false)
const modelError = ref('')
const selectedMode = ref<'demo' | 'live' | null>(null)
const readMode = computed(() => selectedMode.value ?? (readModel.value?.live_enabled ? 'live' : 'demo'))
const modelProvider = computed(() => readModel.value?.providers.find(option => option.id === readModel.value?.provider))
const waitingForMode = computed(() => loadingModel.value || (!readModel.value && selectedMode.value === null))
const askError = ref('')
const imageDialog = ref<HTMLDialogElement | null>(null)
const image = ref('')
const imageFailed = ref(false)
const bodyStart = ref<HTMLElement | null>(null)
let revision = 0

const tabs = [
  { id: 'body', label: '正文', icon: BookOpen },
  { id: 'comments', label: '查看评论', icon: MessageSquare },
  { id: 'ask', label: '问这篇', icon: MessageCircleQuestion },
] as const
const sourceLink = computed(() => zhihuUrl(current.value?.item.url ?? props.target?.url))

function display(page: Page | null) {
  revision++
  current.value = page
  comments.value = null
  loadingComments.value = false
  commentError.value = ''
  openingLink.value = false
  saving.value = false
  saveError.value = ''
  expired.value = false
  askError.value = ''
  selectedMode.value = null
}

async function preview(link: string) {
  const { saved, preview_id: previewId, ...item } = await agentApi<ReadingPreview>(`/library/preview?${new URLSearchParams({ url: link })}`)
  return { item, saved, previewId }
}

async function load() {
  trail.value = []
  loadError.value = ''
  loading.value = false
  if (props.item) {
    display({ item: props.item, saved: true, tab: 'body' })
    return
  }
  display(null)
  const target = props.target
  if (!target) return
  const version = revision
  loading.value = true
  try {
    const page = await preview(target.url)
    if (revision !== version) return
    display({ ...page, tab: 'body' })
    loading.value = false
  } catch (reason) {
    if (revision === version) loadError.value = errorText(reason)
  } finally {
    if (revision === version) loading.value = false
  }
}

watch(() => [props.item, props.target], load, { immediate: true })
onBeforeUnmount(() => { revision++ })

async function fetchComments() {
  const page = current.value
  if (!page || loadingComments.value) return
  const version = revision
  loadingComments.value = true
  commentError.value = ''
  const path = page.saved ? `/library/${page.item.id}/comments`
    : `/library/preview/comments?${new URLSearchParams({ url: page.item.url ?? '' })}`
  try {
    const result = await agentApi<{ comments: LibraryComment[] }>(path)
    if (revision === version) comments.value = result.comments
  } catch (reason) {
    if (revision === version) commentError.value = errorText(reason)
  } finally {
    if (revision === version) loadingComments.value = false
  }
}

function selectTab(tab: Tab) {
  if (!current.value) return
  current.value.tab = tab
  if (tab === 'comments' && comments.value === null) void fetchComments()
  if (tab === 'ask') void loadReaderModel()
}

async function loadReaderModel() {
  if (loadingModel.value) return
  loadingModel.value = true
  modelError.value = ''
  try {
    const status = await agentApi<ModelStatus>('/settings/model')
    readModel.value = status
    if (!status.live_enabled && selectedMode.value === 'live') selectedMode.value = 'demo'
  } catch (reason) {
    readModel.value = null
    if (selectedMode.value === 'live') selectedMode.value = null
    modelError.value = errorText(reason)
  } finally { loadingModel.value = false }
}

async function follow(link: string) {
  const page = current.value
  if (!page || openingLink.value) return
  const version = revision
  openingLink.value = true
  commentError.value = ''
  try {
    const next = await preview(link)
    if (revision !== version || page.item.id === next.item.id) return
    trail.value.push(page)
    display({ ...next, tab: 'body' })
    await nextTick()
    bodyStart.value?.focus()
  } catch (reason) {
    if (revision === version) commentError.value = errorText(reason)
  } finally {
    if (revision === version) openingLink.value = false
  }
}

function previous() {
  const page = trail.value.pop()
  if (!page) return
  display(page)
  if (page.tab === 'comments') void fetchComments()
}

async function save() {
  const page = current.value
  if (!page || page.saved || saving.value) return
  const version = revision
  saving.value = true
  saveError.value = ''
  try {
    const item = await agentApi<LibraryItem>('/library', { method: 'POST', body: { source: 'zhihu', preview_id: page.previewId } })
    emit('saved', item)
    if (revision === version) {
      page.item = item
      page.saved = true
    }
  } catch (reason) {
    if (revision === version) {
      saveError.value = errorText(reason)
      expired.value = errorStatus(reason) === 410
    }
  } finally {
    if (revision === version) saving.value = false
  }
}

async function ask() {
  const page = current.value
  const text = question.value.trim()
  const mode = readMode.value
  if (!page || !text || expired.value || loading.value || waitingForMode.value || (mode === 'live' && !readModel.value?.live_enabled)) return
  const id = page.item.id
  const token = begin(id)
  if (!token) return
  const version = revision
  askError.value = ''
  const path = page.saved ? `/library/${id}/ask` : '/library/preview/ask'
  const body = { question: text, mode, consent: mode === 'live', ...(page.saved ? {} : { preview_id: page.previewId }) }
  let answer: ResearchResult | undefined
  try {
    const result = await agentApi<ResearchResult>(path, { method: 'POST', body })
    if (mode === 'live' && result.generation?.mode !== 'live') {
      if (revision === version) askError.value = '服务未确认模型调用，请刷新页面后重试。'
      return
    }
    answer = result
  } catch (reason) {
    if (revision === version) {
      askError.value = errorText(reason)
      expired.value = errorStatus(reason) === 410
    }
  } finally {
    finish(id, token, answer && { question: text, result: answer })
  }
}

function clearQuestions() {
  const item = current.value?.item
  if (!item || asking.value) return
  clear(item.id)
  askError.value = ''
}

async function reloadPreview() {
  const page = current.value
  if (!page?.item.url || loading.value) return
  const version = revision
  loading.value = true
  try {
    const next = await preview(page.item.url)
    if (revision === version) display({ ...next, tab: page.tab })
  } catch (reason) {
    if (revision === version) saveError.value = errorText(reason)
  } finally { loading.value = false }
}

function openImage(link: string) {
  image.value = link
  imageFailed.value = false
  imageDialog.value?.showModal()
}
</script>

<template>
  <article class="reader">
    <div v-if="(loading && !current) || loadError" class="space-y-3">
      <h2>{{ target?.title }}</h2>
      <p v-if="loading" class="caption" role="status">正在读取知乎正文…</p>
      <p v-else class="notice error" role="alert">{{ loadError }}</p>
      <div v-if="loadError" class="flex flex-wrap items-center gap-3">
        <button type="button" class="secondary" @click="load">重试</button>
        <a v-if="sourceLink" :href="sourceLink" target="_blank" rel="noopener noreferrer" class="text-link">去知乎查看 <ExternalLink :size="14" /></a>
      </div>
    </div>
    <template v-else-if="current">
      <button v-if="trail.length" type="button" class="text-link mb-3" @click="previous"><ArrowLeft :size="15" /> 返回上一篇</button>
      <header class="reader-header">
        <h2 ref="bodyStart" tabindex="-1">{{ current.item.title }}</h2>
        <div class="reader-meta">
          <span class="caption">{{ current.item.author || (current.item.source === 'note' ? '手动添加' : '知乎正文') }}</span>
          <div class="reader-actions">
            <button v-if="!current.saved" type="button" class="text-link" :disabled="saving || expired || loading" @click="save"><Bookmark :size="15" />{{ saving ? '保存中…' : '保存到阅读库' }}</button>
            <span v-else-if="target || trail.length" class="caption inline-flex items-center gap-1"><Check :size="15" />已在阅读库</span>
            <a v-if="sourceLink" :href="sourceLink" class="text-link" target="_blank" rel="noopener noreferrer">去知乎查看 <ExternalLink :size="14" /></a>
            <slot v-if="!trail.length" name="actions" />
          </div>
        </div>
        <p v-if="saveError" class="notice error mt-3" role="alert">{{ saveError }}</p>
        <button v-if="expired" type="button" class="secondary mt-3" :disabled="loading" @click="reloadPreview">{{ loading ? '正在加载…' : '重新加载正文' }}</button>
        <slot v-if="!trail.length" name="notice" />
      </header>
      <div class="view-tabs mt-3" role="group" aria-label="阅读视图">
        <template v-for="tab in tabs" :key="tab.id">
          <button v-if="tab.id !== 'comments' || current.item.source !== 'note'" type="button" :aria-pressed="current.tab === tab.id" @click="selectTab(tab.id)">
            <component :is="tab.icon" :size="16" />{{ tab.label }}
          </button>
        </template>
      </div>
      <ArticleBody v-if="current.tab === 'body'" class="mt-4" :body="current.item.body" />
      <section v-else-if="current.tab === 'comments'" class="mt-4" aria-label="评论区">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div><h3>{{ current.item.source === 'demo' ? '示例评论' : '知乎评论' }}</h3><p v-if="current.item.source === 'zhihu'" class="caption">最多展示 10 条主评论及部分回复。</p></div>
          <button type="button" class="secondary" :disabled="loadingComments" @click="fetchComments">刷新评论</button>
        </div>
        <p v-if="loadingComments" role="status" class="caption mt-3">正在读取评论…</p>
        <p v-if="commentError" role="alert" class="notice error mt-3">{{ commentError }}</p>
        <p v-if="openingLink" role="status" class="caption mt-3">正在读取链接中的正文…</p>
        <CommentThread v-if="comments?.length" class="mt-4" :rows="comments" @open-article="follow" @open-image="openImage" />
        <p v-else-if="comments" class="caption mt-4">这篇暂时没有可显示的评论。</p>
      </section>
      <section v-else class="mt-4 space-y-3" aria-label="单篇问答">
        <QuestionComposer v-model="question" :suggestions="suggestions" :suggesting="suggesting"
          :busy="asking" :blocked="expired || loading || waitingForMode"
          :submit-label="readMode === 'live' ? '调用模型回答' : '查看原文摘录'"
          :busy-label="readMode === 'live' ? '模型正在回答…' : '正在查找原文…'" @submit="ask">
          <template #options>
            <span v-if="loadingModel" class="caption" role="status">正在读取模型设置…</span>
            <div v-else-if="readModel?.live_enabled" class="segmented" role="group" aria-label="当前文章回答方式">
              <button type="button" :disabled="asking" :aria-pressed="readMode === 'live'" @click="selectedMode = 'live'">模型问答</button>
              <button type="button" :disabled="asking" :aria-pressed="readMode === 'demo'" @click="selectedMode = 'demo'">原文摘录</button>
            </div>
            <div v-else-if="modelError" class="flex flex-wrap items-center gap-2">
              <span class="caption" role="alert">模型设置读取失败。{{ modelError }}</span>
              <button type="button" class="text-link" :disabled="asking" @click="loadReaderModel">重试</button>
              <button v-if="selectedMode === null" type="button" class="text-link" @click="selectedMode = 'demo'">使用本地摘录</button>
            </div>
            <template v-else>
              <span class="caption">当前文章 · 本地摘录</span>
              <NuxtLink v-if="readModel?.live_allowed" to="/settings" class="text-link">配置实时问答</NuxtLink>
            </template>
            <p v-if="!loadingModel && readMode === 'live' && modelProvider" class="caption">
              {{ modelProvider.label }} · {{ readModel?.model }}。点击「调用模型回答」会发送问题和本篇相关原文，可能计费。
            </p>
          </template>
        </QuestionComposer>
        <p v-if="askError" role="alert" class="notice error">{{ askError }}</p>
        <section v-if="turns.length" class="space-y-3" aria-label="本篇问答记录">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <h3>问答记录 <span class="caption">· {{ turns.length }} 条</span></h3>
            <button type="button" class="text-link" :disabled="asking" @click="clearQuestions">清空本篇记录</button>
          </div>
          <p class="caption">{{ browserSaved ? '临时保存在当前标签页，距上次回答满 24 小时后清除。' : '浏览器存储不可用，记录暂存于当前页面会话。' }}</p>
          <details v-for="(turn, index) in turns" :key="turn.id" :open="index === 0" class="reader-turn">
            <summary>{{ turn.question }}</summary>
            <div class="space-y-3 mt-3">
              <p class="caption" data-testid="reader-answer-mode">
                {{ turn.result.generation?.mode === 'live' ? `模型回答 · ${turn.result.generation.provider} / ${turn.result.generation.model}` : '本地摘录 · 当前正文' }}
              </p>
              <p class="notice whitespace-pre-wrap">{{ turn.result.answer }}</p>
              <p v-if="turn.result.citation_warning" class="notice error">模型回答中的引用尚无法完整核对，请查看原文。</p>
              <div v-for="source in turn.result.sources" :key="source.citation_id" class="space-y-2">
                <div class="flex flex-wrap items-center gap-2"><strong>[{{ source.citation_id }}] {{ source.title }}</strong><button type="button" class="text-link" @click="selectTab('body')">查看本文正文</button></div>
                <blockquote v-for="quote in source.quotes" :key="quote" class="quote-box whitespace-pre-wrap">{{ quote }}</blockquote>
              </div>
              <SaveResearchNote v-if="turn.result.completed && turn.result.sources.length" :question="turn.question" :result="turn.result"
                :article-title="current.item.title" :article-url="current.item.url" @saved="emit('saved', $event)" />
            </div>
          </details>
        </section>
      </section>
    </template>
    <dialog ref="imageDialog" aria-label="评论图片" class="fixed inset-0 m-auto max-h-[90dvh] w-[min(92vw,900px)] overflow-auto rounded-xl bg-white p-4 shadow-xl backdrop:bg-slate-950/60" @close="image = ''">
      <div class="mb-3 flex items-center justify-between gap-3"><h2>评论图片</h2><form method="dialog"><button class="secondary">关闭图片</button></form></div>
      <p v-if="imageFailed" class="notice">图片暂时加载失败。</p>
      <img v-else-if="image" :src="image" alt="评论附图" referrerpolicy="no-referrer" class="mx-auto max-h-[72dvh] max-w-full object-contain" @error="imageFailed = true" />
    </dialog>
  </article>
</template>

<style scoped>
.reader { min-width: 0; }
.reader-header h2 { font-size: clamp(19px, 2.6vw, 27px); line-height: 1.4; overflow-wrap: anywhere; }
.reader-meta, .reader-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 15px; }
.reader-meta { margin-top: 9px; justify-content: space-between; }
.reader-turn { padding: 10px 12px; border: 1px solid var(--line); border-radius: 10px; }
.reader-turn summary { cursor: pointer; font-weight: 650; overflow-wrap: anywhere; }
</style>
