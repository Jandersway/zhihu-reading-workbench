<script setup lang="ts">
import { ArrowRight, Compass, ExternalLink, RefreshCw, Search, SlidersHorizontal } from 'lucide-vue-next'
import type { Grade, ReaderTarget } from '~/types/agent'
import type { CollectionChange, CollectionStatus, FeedSnapshot, ReadingType, SearchItem, SearchResult, Topic, TrainingStatus } from '~/types/discovery'

type LoadAction = 'load' | 'refresh' | 'goal'

const route = useRoute()
const feed = useDiscoveryFeed()
const routeKeyword = computed(() => typeof route.query.q === 'string' ? route.query.q.slice(0, 100).trim() : '')
const routeTopic = computed(() => typeof route.query.topic === 'string' ? route.query.topic : '')
const query = ref(routeKeyword.value)
const mode = ref<'feed' | 'search'>(routeKeyword.value ? 'search' : 'feed')
const searchBar = ref<{ focus: () => void } | null>(null)
const manualGoal = ref<string | null>(null)
const metadata = ref<{ topics: Topic[]; formats: ReadingType[] } | null>(null)
const result = ref<SearchResult | null>(null)
const suggestion = computed(() => {
  if (result.value?.source !== mode.value || mode.value === 'search' && query.value.trim() !== routeKeyword.value) return null
  return result.value.goal_suggestion
})
const goalInput = computed({
  get: () => manualGoal.value ?? suggestion.value?.text ?? '',
  set: value => { manualGoal.value = value },
})
const suggestionReason = computed(() => {
  const item = suggestion.value
  if (!item) return ''
  const evidence = item.signal === 'rating' ? '你标记为「明确想读」的' : '阅读库中的'
  if (item.source === 'query') return item.title
    ? `根据这次搜索，参考${evidence}《${item.title}》。`
    : '根据这次搜索生成。'
  if (item.source === 'history') return `参考${evidence}《${item.title}》。`
  if (item.source === 'feed') return `从本批候选中找到 ${item.count} 篇同主题文章，据此生成建议。`
  return '目前线索较少。你可以填写目标，或收藏、评价感兴趣的文章。'
})
const loading = ref(false)
const loadAction = ref<LoadAction>('load')
const error = ref('')
const syncing = ref(false)
const collectionError = ref('')
const collections = ref<CollectionStatus | null>(null)
const comparison = ref<CollectionChange[]>([])
const changes = computed(() => comparison.value.filter(row => row.before !== row.after).length)
const controlsLocked = computed(() => !!rating.value || syncing.value || changingVisibility.value)
const reader = ref<ReaderTarget | null>(null)
const rating = ref('')
const rated = ref('')
const ratingError = ref('')
const batchRatings = ref<Record<string, Grade>>({})
const training = ref<TrainingStatus | null>(null)
const nextLearnedAvailable = ref(false)
let triedInitialSync = false
const changingVisibility = ref(false)
const visibilityError = ref('')
const lastHidden = ref<FeedSnapshot['lastHidden']>(null)
let searchVersion = 0
const grades = [{ value: 0, label: '不想读' }, { value: 1, label: '可能想读' }, { value: 2, label: '明确想读' }] as const
const topicLabel = (id: string | null) => metadata.value?.topics.find(entry => entry.id === id)?.label ?? '其他主题'
const formatLabel = (id: string | null) => metadata.value?.formats.find(entry => entry.id === id)?.label ?? '类型待判定'
const externalSearch = computed(() => `https://www.zhihu.com/search?type=article&q=${encodeURIComponent(result.value?.query || query.value)}`)

onMounted(() => {
  void loadResults()
  agentApi<{ topics: Topic[]; formats: ReadingType[] }>('/discover/topics')
    .then(data => { metadata.value = data })
    .catch(() => {})
})

watch([routeKeyword, routeTopic], ([q]) => {
  if (route.path !== '/discover') return
  query.value = q
  mode.value = q ? 'search' : 'feed'
  void loadResults()
})

onBeforeUnmount(() => { searchVersion++ })

async function selectMode(next: 'feed' | 'search') {
  if (mode.value === next || controlsLocked.value) return
  mode.value = next
  if (next === 'feed') {
    if (routeKeyword.value) await navigateTo({ path: '/discover', query: {} })
    else if (result.value?.source !== 'feed' && !loading.value) await loadResults()
    return
  }
  await nextTick()
  searchBar.value?.focus()
}

function submitSearch(keyword: string, topic = '') {
  if (!keyword || controlsLocked.value) return
  if (keyword === routeKeyword.value && topic === routeTopic.value) {
    if (!loading.value) return loadResults()
    return
  }
  return navigateTo({ path: '/discover', query: { q: keyword, topic: topic || undefined } })
}

function applyPreferences() {
  if (mode.value === 'feed') return loadResults('goal')
  const keyword = query.value.trim() || routeKeyword.value
  return submitSearch(keyword, keyword === routeKeyword.value ? routeTopic.value : '')
}

function restoreSuggestion() {
  manualGoal.value = null
  return applyPreferences()
}

function resetBatchState() {
  comparison.value = []
  rated.value = ''
  ratingError.value = ''
  batchRatings.value = {}
  training.value = null
  nextLearnedAvailable.value = false
  visibilityError.value = ''
  lastHidden.value = null
}

function rememberFeed() {
  if (result.value?.source !== 'feed') return
  feed.save({
    result: result.value, batchRatings: batchRatings.value, comparison: comparison.value,
    training: training.value, nextLearnedAvailable: nextLearnedAvailable.value, lastHidden: lastHidden.value,
  })
}

async function loadResults(action: LoadAction = 'load') {
  const keyword = routeKeyword.value
  const auto = !keyword
  const version = ++searchVersion
  loadAction.value = action
  error.value = ''
  reader.value = null
  const savedFeed = auto && action === 'load' ? feed.snapshot.value : null
  if (savedFeed) {
    resetBatchState()
    result.value = savedFeed.result
    batchRatings.value = savedFeed.batchRatings
    comparison.value = savedFeed.comparison
    training.value = savedFeed.training
    nextLearnedAvailable.value = savedFeed.nextLearnedAvailable
    lastHidden.value = savedFeed.lastHidden
    collections.value = savedFeed.result.zhihu_collections ?? null
    manualGoal.value = savedFeed.result.goal_origin === 'manual' ? savedFeed.result.goal : null
    loading.value = false
    return
  }

  loading.value = true
  const previousFeed = auto && result.value?.source === 'feed' ? result.value : null
  if (!previousFeed) {
    result.value = null
    resetBatchState()
  }
  const goal = manualGoal.value?.trim()
  if (!goal) manualGoal.value = null
  const params = new URLSearchParams({ q: keyword })
  if (goal) params.set('goal', goal)
  if (auto) params.set('auto', 'true')
  if (!auto && routeTopic.value) params.set('topic', routeTopic.value)
  let loaded = false
  try {
    const data = action === 'goal' && previousFeed
      ? await agentApi<SearchResult>(`/discover/batches/${previousFeed.batch_id}/rerank?${new URLSearchParams({ goal: goal ?? '' })}`, { method: 'POST' })
      : await agentApi<SearchResult>(`/discover/articles?${params}`)
    if (version === searchVersion) {
      resetBatchState()
      result.value = data
      collections.value = data.zhihu_collections ?? null
      rememberFeed()
      loaded = true
    }
  } catch (reason) {
    if (version === searchVersion) error.value = errorText(reason)
  } finally {
    if (version === searchVersion) {
      loading.value = false
      if (auto && loaded && action !== 'goal' && !collections.value?.semantic_ready && !triedInitialSync) {
        triedInitialSync = true
        void syncCollections()
      }
    }
  }
}

const visibleResult = computed(() => result.value?.source === mode.value ? result.value : null)
const showRequestState = computed(() => mode.value === 'feed' || Boolean(routeKeyword.value))

async function syncCollections() {
  if (loading.value || controlsLocked.value) return
  const version = searchVersion
  syncing.value = true
  collectionError.value = ''
  try {
    const response = await agentApi<{ zhihu_collections: CollectionStatus; comparison: CollectionChange[]; batch: SearchResult | null }>(
      '/discover/collections/sync', { method: 'POST', body: { batch_id: visibleResult.value?.batch_id } },
    )
    if (version !== searchVersion) return
    collections.value = response.zhihu_collections
    if (response.batch) {
      resetBatchState()
      result.value = response.batch
    }
    comparison.value = response.comparison
    if (result.value) result.value.zhihu_collections = response.zhihu_collections
    rememberFeed()
  } catch (reason) {
    if (version === searchVersion) collectionError.value = errorText(reason)
  } finally { syncing.value = false }
}

function changeReason(row: CollectionChange) {
  if (row.bonus > 0 && row.favorite_title) {
    const movement = row.before === row.after ? '，名次不变' : row.before < row.after ? '，其他篇加分更多' : ''
    return `与收藏《${row.favorite_title}》标题相近，+${row.bonus.toFixed(4)} 分${movement}`
  }
  if (row.favorite_title) return `命中《${row.favorite_title}》，已有同等或更高的阅读库加分`
  return row.before !== row.after ? '自身得分不变，其他文章加分后改变了相对位置' : '没有额外收藏加分，名次不变'
}

async function rate(item: SearchItem, grade: Exclude<Grade, null>) {
  if (controlsLocked.value || loading.value) return
  const batch = result.value?.batch_id
  if (!batch) return
  rating.value = item.candidate_id
  ratingError.value = ''
  try {
    const value = batchRatings.value[item.candidate_id] === grade ? null : grade
    const updated = await agentApi<{ grade: Grade; training: TrainingStatus; trained_available: boolean }>('/discover/rating', {
      method: 'PUT', body: { candidate_id: item.candidate_id, batch_id: batch, grade: value },
    })
    if (result.value?.batch_id === batch) {
      batchRatings.value[item.candidate_id] = updated.grade
      rated.value = item.candidate_id
      training.value = updated.training
      nextLearnedAvailable.value = updated.trained_available
      rememberFeed()
    }
  } catch (reason) { ratingError.value = errorText(reason) }
  finally { rating.value = '' }
}

const trainingSummary = computed(() => training.value ?? visibleResult.value?.ranking_model)
const stageLabel = computed(() => {
  const stage = visibleResult.value?.ranking_model.stage
  return stage === 'learned' ? '人工反馈模型' : stage === 'semantic' ? '收藏语义排序' : '现有规则排序'
})
const signalLabels: Record<string, string> = {
  semantic: '收藏语义匹配', tutorial: '动手教程', principle: '原理解析', case: '项目案例', zhihu: '知乎原序',
}

function modelReason(item: SearchItem) {
  const parts: string[] = []
  if (item.semantic_title) parts.push(`相近收藏《${item.semantic_title}》，余弦 ${item.semantic_cosine?.toFixed(3)}`)
  if (visibleResult.value?.ranking_model.experiment_available) {
    const strongest = Object.entries(item.model_contributions)
      .filter(([name, contribution]) => name !== 'semantic' && Math.abs(contribution) >= 0.0001)
      .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))[0]
    if (strongest) parts.push(`${signalLabels[strongest[0]]}${strongest[1] > 0 ? '加分' : '降权'}`)
  }
  if (item.rule_rank !== item.model_rank) parts.push(`在目标同档中${item.model_rank < item.rule_rank ? '上调' : '下调'} ${Math.abs(item.rule_rank - item.model_rank)} 位`)
  else if (!parts.length) parts.push('与现有规则同位')
  if (item.original_rank !== item.rule_rank) {
    const reason = item.matched_terms.length ? `命中「${item.matched_terms.slice(0, 3).join('、')}」`
      : item.dislike_title ? '相似不想读反馈'
      : item.official_favorite_title || item.favorite_title ? '收藏匹配' : '同批其他文章调整'
    parts.push(`规则阶段 · ${reason}`)
  }
  return parts.join('；')
}

async function setVisibility(item: SearchItem, hidden: boolean) {
  const batch = visibleResult.value
  if (!batch || batch.source !== 'feed' || controlsLocked.value || loading.value) return
  const version = searchVersion
  changingVisibility.value = true
  visibilityError.value = ''
  try {
    await agentApi('/discover/visibility', { method: 'PUT', body: { candidate_id: item.candidate_id, hidden } })
    if (version !== searchVersion || batch.batch_id !== result.value?.batch_id) return
    if (hidden) {
      const index = batch.items.findIndex(row => row.candidate_id === item.candidate_id)
      if (index < 0) return
      batch.items.splice(index, 1)
      lastHidden.value = { item, index, batch: batch.batch_id }
    } else if (lastHidden.value?.batch === batch.batch_id) {
      batch.items.splice(lastHidden.value.index, 0, item)
      lastHidden.value = null
    }
    comparison.value = []
    rememberFeed()
  } catch (reason) {
    if (version === searchVersion) visibilityError.value = errorText(reason)
  } finally { changingVisibility.value = false }
}

</script>

<template>
  <div class="page-stack">
    <header class="page-heading">
      <h1>发现文章</h1>
      <p>浏览按兴趣排序的内容，或搜一个主题深入读。</p>
    </header>
    <section class="panel discovery-controls" aria-label="找文章">
      <div class="discovery-tabs" role="group" aria-label="发现方式">
        <button type="button" :aria-pressed="mode === 'feed'" :disabled="controlsLocked" @click="selectMode('feed')">
          <Compass :size="17" />为你推荐
        </button>
        <button type="button" :aria-pressed="mode === 'search'" :disabled="controlsLocked" @click="selectMode('search')">
          <Search :size="17" />主题搜索
        </button>
      </div>

      <div class="discovery-entry">
        <template v-if="mode === 'search'">
          <ArticleSearchBar ref="searchBar" v-model="query" :busy="loading && !!routeKeyword" :disabled="controlsLocked" @search="submitSearch" />
        </template>
        <div v-else class="feed-entry">
          <p>当前推荐会保留，点击「刷新推荐」可换一批。</p>
          <button type="button" class="secondary" :disabled="loading || controlsLocked" @click="loadResults('refresh')">
            <RefreshCw :size="16" />刷新推荐
          </button>
        </div>
        <div class="collections-control">
          <button type="button" class="secondary" :disabled="loading || controlsLocked" @click="syncCollections">
            <RefreshCw :size="15" />{{ syncing ? '正在同步收藏…' : collections ? '更新知乎收藏' : '同步知乎收藏' }}
          </button>
          <p v-if="collections" class="caption">
            知乎收藏 · {{ collections.collections }} 个收藏夹，{{ collections.count }} 篇可用于排序
            <span v-if="collections.truncated"> · 部分同步</span>
          </p>
          <p v-else class="caption">同步自己在知乎保存的文章，用于推荐排序。</p>
        </div>
        <p v-if="collectionError" class="notice error" role="alert">{{ collectionError }}</p>
        <details class="preferences">
          <summary><SlidersHorizontal :size="15" />调整阅读目标</summary>
          <form class="preference-form" @submit.prevent="applyPreferences">
            <label class="grid gap-1.5">
              <span class="field-label">阅读目标</span>
              <input v-model="goalInput" aria-describedby="goal-help" class="field min-w-0" maxlength="160"
                :placeholder="loading ? '正在生成建议…' : '搜索主题生成建议，也可以自己填写'" />
            </label>
            <button class="secondary" :disabled="loading || controlsLocked || (mode === 'search' && !query.trim() && !routeKeyword)">应用目标</button>
            <div id="goal-help" class="goal-help caption" aria-live="polite">
              <span v-if="manualGoal !== null">{{ visibleResult?.goal_origin === 'manual' && visibleResult.goal === manualGoal.trim()
                ? '本轮正在使用你填写的目标。' : '点击「应用目标」使用你填写的目标。' }}</span>
              <span v-else-if="suggestionReason">{{ suggestionReason }}</span>
              <span v-else-if="!loading && mode === 'search'">搜索后会自动生成阅读目标。</span>
              <button v-if="manualGoal !== null" type="button" class="text-link"
                :disabled="loading || controlsLocked" @click="restoreSuggestion">恢复智能建议</button>
            </div>
          </form>
        </details>
      </div>
    </section>

    <p v-if="loading && showRequestState" class="panel task-panel caption" role="status">{{ loadAction === 'goal' && visibleResult ? '正在按阅读目标重排这批文章…' : '正在从知乎获取文章并排序…' }}</p>
    <div v-else-if="error && showRequestState" class="notice error" role="alert">
      {{ error }} <button type="button" class="text-link" @click="loadResults(loadAction)">重试</button>
    </div>

    <section v-if="visibleResult" class="reading-results" aria-labelledby="results-title">
      <div class="results-heading">
        <div>
          <h2 id="results-title">{{ visibleResult.source === 'feed' ? '推荐给你' : '“' + visibleResult.query + '” 的搜索结果' }}</h2>
          <p class="caption">{{ visibleResult.items.length }} 篇文章<span v-if="visibleResult.filtered_ads"> · 已过滤 {{ visibleResult.filtered_ads }} 条推广</span></p>
          <p v-if="visibleResult.goal" class="caption mt-1">
            {{ visibleResult.goal_origin === 'manual' ? '本轮目标' : '智能阅读目标' }} · {{ visibleResult.goal }}
          </p>
          <p class="caption mt-1">当前排序 · {{ stageLabel }}</p>
        </div>
        <NuxtLink v-if="visibleResult.items.length && visibleResult.ranking_model.semantic_status === 'ready'"
          class="secondary" :to="{ path: '/discover-evaluation', query: { batch: visibleResult.batch_id } }">评测这批</NuxtLink>
      </div>
      <details v-if="visibleResult.items.length" class="panel collection-comparison">
        <summary>同一批候选的三种排序</summary>
        <p v-if="trainingSummary" class="caption mt-2">
          可比较评分来自 {{ trainingSummary.eligible_batches }} 批，累计 {{ trainingSummary.eligible_pairs }} 对。
          <template v-if="!trainingSummary.validation">训练起点为 {{ trainingSummary.min_batches }} 批、{{ trainingSummary.min_pairs }} 对，并按时间留出验证批次。</template>
          <template v-else>
            留出 {{ trainingSummary.validation_batches }} 批 · 已评分候选 NDCG@3
            · 知乎 {{ trainingSummary.validation.zhihu.toFixed(3) }}
            · 规则 {{ trainingSummary.validation.rules.toFixed(3) }}
            · 收藏语义 {{ trainingSummary.validation.cold_start.toFixed(3) }}
            · 训练模型 {{ trainingSummary.validation.learned.toFixed(3) }}。
          </template>
        </p>
        <p v-if="visibleResult.ranking_model.semantic_status === 'unavailable'" class="caption mt-2">收藏已同步，本机语义模型暂不可用，当前保留词项匹配。</p>
        <p v-if="training && nextLearnedAvailable && visibleResult.ranking_model.stage !== 'learned'" class="caption mt-2">新版通过留出评测，下次刷新推荐时使用。</p>
        <p v-if="visibleResult.ranking_model.experiment_available && visibleResult.ranking_model.stage !== 'learned'" class="caption mt-2">训练模型仅供本批对照，推荐列表保留当前排序。</p>
        <div class="table-wrap mt-3">
          <table>
            <thead><tr><th>文章</th><th>知乎原序</th><th>现有规则</th><th>{{ visibleResult.ranking_model.experiment_available ? '模型排序' : stageLabel }}</th><th>名次变化依据</th></tr></thead>
            <tbody>
              <tr v-for="item in visibleResult.items" :key="item.candidate_id">
                <td><button type="button" class="text-link" @click="reader = item">{{ item.title }}</button></td>
                <td>{{ item.original_rank }}</td><td>{{ item.rule_rank }}</td><td>{{ item.model_rank }}</td><td>{{ modelReason(item) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </details>
      <p v-if="lastHidden" class="caption" role="status">
        已从首页推荐隐藏《{{ lastHidden.item.title }}》。
        <button type="button" class="text-link" :disabled="controlsLocked" @click="setVisibility(lastHidden.item, false)">撤销</button>
      </p>
      <p v-if="visibilityError" class="notice error" role="alert">{{ visibilityError }}</p>
      <details v-if="comparison.length" class="panel collection-comparison">
        <summary>知乎收藏排序对照 · {{ changes }} / {{ comparison.length }} 篇名次变动</summary>
        <div class="table-wrap mt-3">
          <table>
            <thead><tr><th>文章</th><th>接入前</th><th>接入后</th><th>原因</th></tr></thead>
            <tbody>
              <tr v-for="row in comparison" :key="row.candidate_id">
                <td>{{ row.title }}</td><td>{{ row.before }}</td><td>{{ row.after }}</td><td>{{ changeReason(row) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </details>
      <p v-if="ratingError" class="notice error" role="alert">{{ ratingError }}</p>
      <div v-if="!visibleResult.items.length" class="panel task-panel">
        <h3>这次没有找到文章</h3>
        <p v-if="visibleResult.source === 'search'" class="caption mt-2">可以换一个关键词再试。</p>
      </div>
      <ul v-else class="panel results-list">
        <li v-for="item in visibleResult.items" :key="item.candidate_id" class="article-row">
          <div class="article-labels">
            <span>{{ topicLabel(item.topic) }}</span>
            <span v-if="item.reading_type" :title="item.format_terms.join('、')">{{ formatLabel(item.reading_type) }}</span>
            <span v-if="item.suspected_promotion" class="promotion-hint">疑似推广</span>
          </div>
          <h3><button type="button" class="article-title" @click="reader = item">{{ item.title }}</button></h3>
          <p v-if="item.author" class="caption article-author">{{ item.author }}</p>
          <p v-if="item.excerpt" class="article-excerpt">{{ item.excerpt }}</p>
          <div class="article-actions">
            <button type="button" class="text-link read-link" @click="reader = item">读原文 <ArrowRight :size="16" /></button>
            <button v-if="item.suspected_promotion && visibleResult.source === 'feed'" type="button" class="text-link"
              :disabled="controlsLocked || loading" @click="setVisibility(item, true)">隐藏推广</button>
            <details class="article-details">
              <summary>推荐理由与评分</summary>
              <ul class="reason-list" aria-label="推荐理由">
                <li v-if="item.matched_terms.length">本轮命中 {{ item.matched_terms.join('、') }}</li>
                <li v-if="item.suggested_terms?.length">符合智能建议 {{ item.suggested_terms.join('、') }}</li>
                <li v-if="item.favorite_title">与{{ item.favorite_origin === 'note' ? '本地笔记' : item.favorite_origin === 'library' ? '阅读库中的' : '历史收藏' }}《{{ item.favorite_title }}》有相近词</li>
                <li v-if="item.official_favorite_title">
                  与知乎收藏《{{ item.official_favorite_title }}》标题相近{{ item.official_favorite_bonus ? '，获得收藏加分' : '' }}
                </li>
                <li v-if="item.semantic_title">与收藏《{{ item.semantic_title }}》语义接近。</li>
                <li v-if="item.known_grade !== null">已有评价「{{ grades[item.known_grade]?.label }}」</li>
                <li v-else-if="item.rated_title">和你想读的《{{ item.rated_title }}》标题相近</li>
                <li v-if="item.dislike_title">和你标为「不想读」的《{{ item.dislike_title }}》标题高度相似，已降权。</li>
                <li v-if="!item.matched_terms.length && !item.suggested_terms?.length && !item.favorite_title && !item.official_favorite_title && item.known_grade === null && !item.rated_title && !item.dislike_title">来自本轮知乎候选，排序参考知乎原位</li>
              </ul>
              <div class="grade-list" role="group" :aria-label="`评价 ${item.title}`">
                <button v-for="grade in grades" :key="grade.value" type="button" :aria-pressed="batchRatings[item.candidate_id] === grade.value"
                  :disabled="controlsLocked || loading" @click="rate(item, grade.value)">{{ grade.label }}</button>
                <button v-if="visibleResult.source === 'feed' && !item.suspected_promotion" type="button" :disabled="controlsLocked || loading"
                  @click="setVisibility(item, true)">隐藏推广</button>
                <span v-if="rated === item.candidate_id" class="caption" role="status">
                  {{ batchRatings[item.candidate_id] === null ? '已撤销本批评分' : batchRatings[item.candidate_id] === 0 ? '已记入本批，高度相似的文章会在后续推荐中降权' : '已记入本批，下次推荐会参考' }}
                </span>
              </div>
            </details>
          </div>
        </li>
      </ul>
      <a v-if="visibleResult.source === 'search'" :href="externalSearch" target="_blank" rel="noopener noreferrer" class="text-link">
        在知乎查看更多搜索结果 <ExternalLink :size="14" />
      </a>
    </section>
    <ArticleReaderDialog v-model="reader" />
  </div>
</template>

<style scoped>
.discovery-tabs { display: flex; gap: 21px; border-bottom: 1px solid var(--line); padding-inline: clamp(15px, 3vw, 25px); }
.discovery-tabs button { display: inline-flex; align-items: center; gap: 7px; padding-block: 14px 12px; border-bottom: 2px solid transparent; font-weight: 650; color: var(--muted); }
.discovery-tabs button[aria-pressed='true'] { border-color: var(--accent); color: var(--accent); }
.discovery-entry { display: grid; gap: 13px; padding: clamp(15px, 3vw, 25px); }
.feed-entry { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 9px; color: var(--muted); }
.collections-control { display: flex; gap: 8px 12px; align-items: center; flex-wrap: wrap; }
.collections-control .secondary { min-height: 36px; padding-block: 5px; }
.collection-comparison { padding: 10px 13px; }
.collection-comparison > summary { color: var(--accent); font-weight: 670; }
.preferences > summary { display: flex; align-items: center; gap: 6px; min-height: 35px; color: var(--accent); font-weight: 650; }
.preferences > summary::after { content: '+'; margin-left: 3px; }
.preferences[open] > summary::after { content: '−'; }
.preference-form { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: end; gap: 9px; margin-top: 7px; }
.goal-help { grid-column: 1 / -1; display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 12px; }
.reading-results { display: grid; justify-items: start; gap: 13px; min-width: 0; }
.reading-results > :not(a) { width: 100%; }
.results-heading { display: flex; align-items: flex-start; justify-content: space-between; flex-wrap: wrap; gap: 8px; padding-top: 4px; }
.results-heading h2 { font-size: clamp(19px, 3vw, 25px); overflow-wrap: anywhere; }
.article-row { padding: clamp(15px, 3vw, 25px); }
.article-row + .article-row { border-top: 1px solid var(--line); }
.article-labels { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 7px; }
.article-labels span { padding: 3px 8px; border-radius: 5px; color: var(--accent); background: var(--wash); font-size: 12px; }
.article-labels .promotion-hint { color: #784c0f; background: #fff3d9; }
.article-title { text-align: left; font-size: clamp(17px, 2.2vw, 21px); line-height: 1.4; }
.article-title:hover { color: var(--accent); text-decoration: underline; text-underline-offset: 3px; }
.article-author { margin-top: 4px; }
.article-excerpt { margin-top: 7px; color: var(--muted); line-height: 1.68; overflow: hidden; overflow-wrap: anywhere; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.article-actions { margin-top: 10px; display: flex; align-items: baseline; flex-wrap: wrap; gap: 5px 18px; }
.read-link { min-height: 31px; }
.article-details summary { color: var(--muted); font-size: 13px; }
.article-details[open] { flex-basis: 100%; padding: 9px 10px; background: var(--wash); border-radius: 8px; }
.article-details[open] summary { margin-bottom: 7px; }
.reason-list { margin: 6px 0 9px; color: var(--muted); font-size: 13px; }
@media (max-width: 570px) { .preference-form { grid-template-columns: minmax(0, 1fr); } }
</style>
