<script setup lang="ts">
import type { Grade, ReaderTarget } from '~/types/agent'

const methods = [
  { id: 'zhihu', label: '知乎原序' }, { id: 'rules', label: '现有规则' },
  { id: 'bge', label: '规则 + BGE' }, { id: 'e5', label: '规则 + E5' },
] as const
type Method = typeof methods[number]['id']
type Encoder = 'bge' | 'e5'
interface Evidence { title: string; cosine: number; bonus: number }
interface EvaluationItem extends ReaderTarget {
  candidate_id: string; excerpt: string; in_pool: boolean
  ranks: Record<Method, number>; evidence: Partial<Record<Encoder, Evidence>>
  rule_signals: { matched_terms?: string[]; official_favorite_title?: string | null; favorite_title?: string | null;
    known_grade?: Grade; dislike_title?: string | null; suggested_terms?: string[] }
}
interface Metrics {
  judged: number; count: number
  precision_at_3: number | null; mrr_at_3: number | null; pooled_ndcg_at_3: number | null
}
interface Evaluation {
  goal: string; collection_count: number; source: 'feed' | 'search'
  items: EvaluationItem[]; grades: Record<string, Grade>; metrics: Record<Method, Metrics>
  pool_size: number; pool_labeled: number
}

const route = useRoute()
const batchId = computed(() => {
  const value = route.query.batch
  return typeof value === 'string' && /^[a-f0-9]{32}$/.test(value) ? value : ''
})
const path = computed(() => `/discover/evaluations/${batchId.value}`)
const evaluation = ref<Evaluation | null>(null)
const loading = ref(false)
const submitting = ref('')
const error = ref('')
const reveal = ref(false)
const reader = ref<ReaderTarget | null>(null)
const order = ref<Method>('bge')
const ratings = [{ value: 0, label: '不想读' }, { value: 1, label: '可能想读' }, { value: 2, label: '明确想读' }] as const
const sorted = computed(() => [...(evaluation.value?.items ?? [])].sort((a, b) => a.ranks[order.value] - b.ranks[order.value]))

async function load() {
  if (!batchId.value) {
    error.value = '请从发现页选择要评测的一批候选。'
    return
  }
  loading.value = true
  error.value = ''
  try { evaluation.value = await agentApi<Evaluation>(path.value, { method: 'POST' }) }
  catch (reason) { error.value = errorText(reason) }
  finally { loading.value = false }
}

async function rate(item: EvaluationItem, selected: Exclude<Grade, null>) {
  if (!evaluation.value || submitting.value) return
  submitting.value = item.candidate_id
  error.value = ''
  try {
    const grade = evaluation.value.grades[item.candidate_id] === selected ? null : selected
    evaluation.value = await agentApi<Evaluation>(path.value + '/ratings', {
      method: 'PUT', body: { candidate_id: item.candidate_id, grade },
    })
  } catch (reason) { error.value = errorText(reason) }
  finally { submitting.value = '' }
}

function rankChange(move: number) {
  return move === 0 ? '同位' : move > 0 ? `升 ${move} 位` : `降 ${-move} 位`
}

function explainRules(item: EvaluationItem) {
  const { matched_terms, official_favorite_title, favorite_title, known_grade, dislike_title, suggested_terms } = item.rule_signals
  const parts: string[] = []
  if (matched_terms?.length) parts.push(`命中 ${matched_terms.slice(0, 3).join('、')}`)
  if (suggested_terms?.length) parts.push(`符合阅读建议 ${suggested_terms.slice(0, 3).join('、')}`)
  if (official_favorite_title || favorite_title) parts.push(`与《${official_favorite_title || favorite_title}》有相近词`)
  if (known_grade != null) parts.push(`已有「${ratings[known_grade].label}」`)
  if (dislike_title) parts.push(`与「不想读」的《${dislike_title}》相近`)
  const move = item.ranks.zhihu - item.ranks.rules
  return [rankChange(move), ...(parts.length ? parts : [move ? '同批其他文章重排' : '沿用候选相对顺序'])].join(' · ')
}

function explain(item: EvaluationItem, name: Encoder) {
  const change = rankChange(item.ranks.rules - item.ranks[name])
  const clue = item.evidence[name]
  if (!clue?.bonus) return `${change} · 自身无语义加分`
  return `${change} · 最相近收藏《${clue.title}》，余弦 ${clue.cosine.toFixed(3)}，加 ${clue.bonus.toFixed(3)} 分`
}

const format = (value: number | null) => value === null ? '待评' : value.toFixed(3)
onMounted(load)
</script>

<template>
  <div class="page-stack">
    <header class="page-heading">
      <NuxtLink to="/discover" class="text-link">返回发现文章</NuxtLink>
      <h1>同批推荐评测</h1>
      <p v-if="evaluation">
        {{ evaluation.items.length }} 篇固定候选 · {{ evaluation.collection_count }} 篇知乎收藏
        <span v-if="evaluation.goal"> · 本轮目标 {{ evaluation.goal }}</span>
      </p>
    </header>
    <p v-if="loading" class="caption" role="status">正在用同一批候选对比四种排序…</p>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <button v-if="error && !evaluation && batchId" type="button" class="secondary self-start" @click="load">重试</button>
    <template v-if="evaluation">
      <section class="panel task-panel" aria-labelledby="judge-title">
        <div class="flex flex-wrap items-center justify-between gap-3 mb-3">
          <div>
            <h2 id="judge-title">先读文章，再打分</h2>
            <p class="caption">四路前三的并集已评 {{ evaluation.pool_labeled }} / {{ evaluation.pool_size }} 篇。这里的评分用于本次评测。</p>
          </div>
          <button type="button" class="secondary" :aria-expanded="reveal" @click="reveal = !reveal">{{ reveal ? '隐藏名次' : '查看名次与原因' }}</button>
        </div>
        <ul class="judging-list">
          <li v-for="item in evaluation.items" v-show="item.in_pool" :key="item.candidate_id">
            <button type="button" class="text-link judging-title" @click="reader = item">{{ item.title }}</button>
            <p v-if="item.excerpt" class="caption excerpt">{{ item.excerpt }}</p>
            <div class="segmented mt-2" role="group" :aria-label="`评价 ${item.title}`">
              <button v-for="rating in ratings" :key="rating.value" type="button" :disabled="!!submitting"
                :aria-pressed="evaluation.grades[item.candidate_id] === rating.value" @click="rate(item, rating.value)">{{ rating.label }}</button>
            </div>
          </li>
        </ul>
      </section>
      <section v-if="reveal" class="panel task-panel" aria-labelledby="scores-title">
        <h2 id="scores-title">四种排序的结果</h2>
        <p class="caption mt-2">BGE 和 E5 使用相同的收藏、候选和加分上限。两路都保留阅读目标分档，语义分按各自模型在本批的相对位置折算。</p>
        <div class="scroll-table mt-3">
          <table>
            <thead><tr><th>方法</th><th>前三已评</th><th>并集 NDCG@3</th><th>Precision@3</th><th>MRR@3</th></tr></thead>
            <tbody><tr v-for="method in methods" :key="method.id">
              <th scope="row">{{ method.id === 'zhihu' && evaluation.source === 'search' ? '知乎搜索原序' : method.label }}</th>
              <td>{{ evaluation.metrics[method.id].judged }} / {{ evaluation.metrics[method.id].count }}</td>
              <td>{{ evaluation.metrics[method.id].pooled_ndcg_at_3 === null && evaluation.pool_size === evaluation.pool_labeled ? '—' : format(evaluation.metrics[method.id].pooled_ndcg_at_3) }}</td>
              <td>{{ format(evaluation.metrics[method.id].precision_at_3) }}</td>
              <td>{{ format(evaluation.metrics[method.id].mrr_at_3) }}</td>
            </tr></tbody>
          </table>
        </div>
        <p class="caption mt-2">NDCG 在并集全评且有正收益后计算。Precision 和 MRR 以「明确想读」为命中。</p>
        <div class="segmented mt-3" role="group" aria-label="对照表的显示顺序">
          <button v-for="method in methods" :key="method.id" type="button" :aria-pressed="order === method.id" @click="order = method.id">{{ method.label }}</button>
        </div>
        <div class="scroll-table mt-3">
          <table>
            <thead><tr><th>文章</th><th>知乎</th><th>规则</th><th>BGE</th><th>E5</th><th>规则相对知乎</th><th>BGE 相对规则</th><th>E5 相对规则</th></tr></thead>
            <tbody><tr v-for="item in sorted" :key="item.candidate_id">
              <td><button type="button" class="text-link judging-title" @click="reader = item">{{ item.title }}</button></td>
              <td v-for="method in methods" :key="method.id">{{ item.ranks[method.id] }}</td>
              <td>{{ explainRules(item) }}</td><td>{{ explain(item, 'bge') }}</td><td>{{ explain(item, 'e5') }}</td>
            </tr></tbody>
          </table>
        </div>
      </section>
    </template>
    <ArticleReaderDialog v-model="reader" />
  </div>
</template>

<style scoped>
.judging-list { display: grid; gap: 12px; padding: 0; list-style: none; }
.judging-list li { padding: 10px 0; border-top: 1px solid var(--line); }
.judging-title { text-align: left; white-space: normal; }
.excerpt { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.scroll-table { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; text-align: left; font-size: .9rem; }
td, th { padding: 8px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
td:first-child { min-width: 160px; }
</style>
