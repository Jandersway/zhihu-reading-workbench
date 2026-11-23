<script setup lang="ts">
import { ArrowDownRight, ArrowRight, ArrowUpRight, BookOpen, ChartNoAxesColumnIncreasing, Eye, Save } from 'lucide-vue-next'
import type { CaseSummary, Comparison, Form, Grade, Metrics, ReaderTarget, Source } from '~/types/agent'
import { commentLink } from '~/utils/commentLink'

const cases = ref<CaseSummary[]>([])
const selected = ref('')
const source = ref<Source>('ai_proxy')
const metrics = ref<Metrics | null>(null)
const form = ref<Form | null>(null)
const comparison = ref<Comparison | null>(null)
const reader = ref<ReaderTarget | null>(null)
const hadSeenOrder = ref(false)
const revealedCases = new Set<string>()
const loading = ref(true)
const formLoading = ref(false)
const saving = ref(false)
const revealing = ref(false)
const error = ref('')
const message = ref('')
const savedGrades = ref('')
const gradesChanged = computed(() => form.value !== null
  && JSON.stringify(form.value.items.map(item => item.grade)) !== savedGrades.value)
const fields = [
  { k: '1', key: 'hit_at_k', title: 'Hit@1', copy: '首位达到 2 分的批次比例', percent: true },
  { k: '3', key: 'precision_at_k', title: 'Precision@3', copy: '前三个位置中拿到 2 分的比例', percent: true },
  { k: '3', key: 'ndcg_at_k', title: 'NDCG@3', copy: '按相关等级和位置计算的归一化得分', percent: false },
]
const choices: { value: Grade; label: string }[] = [
  { value: 2, label: '明确想读' }, { value: 1, label: '可能想读' }, { value: 0, label: '不想读' }, { value: null, label: '无法判断' },
]
const chosen = computed(() => cases.value.find(row => row.case_id === selected.value))
const metricCards = computed(() => fields.map(field => ({ ...field, pair: metrics.value?.cutoffs[field.k]?.metrics[field.key] })))
const display = (n: number | null | undefined, percent: boolean) => n == null ? '—' : percent ? `${(n * 100).toFixed(1).replace(/\.0$/, '')}%` : n.toFixed(3)
const byId = computed(() => new Map(comparison.value?.candidates.map(row => [row.candidate_id, row]) ?? []))
const tableRows = computed(() => (comparison.value?.scores ?? []).map(score => {
  const item = byId.value.get(score.candidate_id)
  return { ...score, title: item?.title, target: item ? readTarget(item) : null }
}))

function readTarget(item: { title: string; url: string }): ReaderTarget | null {
  const link = item.url ? commentLink(item.url) : undefined
  return link?.kind === 'article' ? { title: item.title, url: link.url } : null
}

function readCandidate(item: { title: string; url: string }) {
  reader.value = readTarget(item)
}

async function metricSource(next: Source) {
  source.value = next
  metrics.value = null
  try {
    const data = await agentApi<Metrics>(`/lab/metrics?source=${next}`)
    if (source.value === next) metrics.value = data
  } catch (reason) { error.value = errorText(reason) }
}

async function selectCase(id: string) {
  selected.value = id
  form.value = null
  comparison.value = null
  hadSeenOrder.value = revealedCases.has(id)
  message.value = ''
  formLoading.value = true
  try {
    const data = await agentApi<Form>(`/lab/cases/${id}`)
    if (selected.value === id) {
      form.value = data
      savedGrades.value = JSON.stringify(data.items.map(item => item.grade))
    }
  } catch (reason) { error.value = errorText(reason) }
  finally { if (selected.value === id) formLoading.value = false }
}

function switchCase(event: Event) {
  const select = event.target as HTMLSelectElement
  if (gradesChanged.value && !window.confirm('当前批次有未保存的评分。仍要切换批次吗？')) {
    select.value = selected.value
    return
  }
  void selectCase(select.value)
}

async function initialize() {
  loading.value = true
  error.value = ''
  try {
    cases.value = await agentApi<CaseSummary[]>('/lab/cases')
    const prior = sessionStorage.getItem('zhihu-revealed')
    if (prior) prior.split(',').forEach(id => revealedCases.add(id))
    await Promise.all([metricSource(source.value), ...(cases.value[0] ? [selectCase(cases.value[0].case_id)] : [])])
  } catch (reason) { error.value = errorText(reason) }
  finally { loading.value = false }
}
onMounted(initialize)

async function save() {
  if (!form.value || saving.value) return
  error.value = ''
  message.value = ''
  saving.value = true
  const data = form.value
  try {
    const result = await agentApi<{ labeled: number }>(`/lab/cases/${data.case_id}/ratings`, {
      method: 'PUT', body: { items: data.items.map(({ candidate_id, grade }) => ({ candidate_id, grade })) },
    })
    const row = cases.value.find(item => item.case_id === data.case_id)
    if (row) row.labeled = result.labeled
    savedGrades.value = JSON.stringify(data.items.map(item => item.grade))
    message.value = `已保存 ${result.labeled} 条人工标签。`
    await metricSource('human')
  } catch (reason) { error.value = errorText(reason) }
  finally { saving.value = false }
}

async function reveal() {
  if (revealing.value || !selected.value || !form.value) return
  const grades = form.value.items.map(item => item.grade)
  if (!hadSeenOrder.value && (grades.includes(null) || gradesChanged.value)
      && !window.confirm('当前评分未全部填写或尚未保存。查看顺序后，继续评分可能受到排名影响。现在查看？')) return
  const id = selected.value
  revealing.value = true
  try {
    const result = await agentApi<Comparison>(`/lab/cases/${id}/comparison`)
    if (selected.value === id) {
      comparison.value = result
      hadSeenOrder.value = true
      revealedCases.add(id)
      sessionStorage.setItem('zhihu-revealed', [...revealedCases].join(','))
    }
  } catch (reason) { error.value = errorText(reason) }
  finally { revealing.value = false }
}
</script>

<template>
  <div class="page-stack">
    <div class="page-heading">
      <span class="tile-icon"><ChartNoAxesColumnIncreasing :size="23" /></span><h1>排序评测</h1>
      <p>同一批历史候选、同一个阅读目标，比较首页原顺序和目标重排。</p>
      <NuxtLink to="/agent-rl" class="text-link mt-3">查看 Agentic RL 实验 <ArrowRight :size="15" /></NuxtLink>
    </div>
    <p v-if="error" class="notice error" role="alert">{{ error }} <button class="text-link" @click="initialize">重试</button></p>
    <section class="panel task-panel" aria-labelledby="metrics">
      <div class="flex flex-wrap items-center justify-between gap-4"><h2 id="metrics">评测指标</h2><div class="segmented" role="group" aria-label="标签来源"><button :aria-pressed="source === 'ai_proxy'" @click="metricSource('ai_proxy')">AI 代理</button><button :aria-pressed="source === 'human'" @click="metricSource('human')">人工评分</button></div></div>
      <p v-if="source === 'ai_proxy'" class="caption mt-3">代理标签按标题和摘要判断主题相关性。四批候选里有三批使用回溯设定的目标。</p>
      <p v-else class="caption mt-3">人工组只使用这里保存的评分。未知保留为空，NDCG 仅在整批标签齐全且有正收益时计算。</p>
      <div class="grid md:grid-cols-3 gap-3 mt-4">
        <div v-for="card in metricCards" :key="card.key" class="metric-card">
          <div class="metric-title">{{ card.title }}</div>
          <div class="metric-values"><span>{{ display(card.pair?.homepage_mean, card.percent) }}</span><ArrowRight :size="17" /><strong>{{ display(card.pair?.challenger_mean, card.percent) }}</strong></div>
          <div class="caption">{{ card.copy }} · {{ card.pair?.paired_cases ?? 0 }} 批可比较</div>
        </div>
      </div>
      <p class="caption mt-3" v-if="metrics">{{ metrics.labeled }} / {{ metrics.candidate_count }} 条已标注。结果只衡量候选排序，最终推荐的效果需另行评测。</p>
    </section>

    <section class="panel task-panel" aria-labelledby="rate">
      <div class="flex flex-wrap items-center justify-between gap-4"><h2 id="rate">给候选打分</h2>
        <label class="flex items-center gap-2 caption">历史批次<select :value="selected" class="field compact" :disabled="loading || formLoading || saving || revealing" @change="switchCase"><option v-for="(entry, i) in cases" :key="entry.case_id" :value="entry.case_id">第 {{ i + 1 }} 批 · {{ entry.labeled }}/{{ entry.count }} 已标注</option></select></label>
      </div>
      <p v-if="chosen" class="notice mt-4"><strong>阅读目标</strong> · {{ chosen.focus }}</p>
      <p v-if="hadSeenOrder" class="caption mt-3">本会话已查看该批次排序，后续评分可能受到先前展示的影响。</p>
      <p v-else class="caption mt-3">候选已打乱。可先读原文再按阅读目标打分，评分后再查看排序对照。</p>
      <p v-if="loading || formLoading" class="notice mt-4" role="status">正在载入候选…</p>
      <form v-if="form" class="mt-3" @submit.prevent="save">
        <fieldset :disabled="saving">
          <div v-for="item in form.items" :key="item.candidate_id" class="rating-item">
            <h3>{{ item.title }}</h3><p class="caption mt-2">{{ item.excerpt || '存档没有摘要，可以选「无法判断」。' }}</p>
            <div class="mt-3 flex flex-wrap items-center gap-3">
              <div class="grade-list" role="group" :aria-label="`为 ${item.title} 打分`"><button v-for="choice in choices" :key="choice.label" type="button" :aria-pressed="item.grade === choice.value" @click="item.grade = choice.value">{{ choice.label }}</button></div>
              <button v-if="readTarget(item)" type="button" class="text-link" @click="readCandidate(item)"><BookOpen :size="15" />读原文</button>
              <span v-else class="caption">存档没有可读取的原文链接</span>
            </div>
          </div>
        </fieldset>
        <div class="flex flex-wrap items-center gap-3 mt-5"><button class="primary" :disabled="saving"><Save :size="16" />{{ saving ? '保存中…' : '保存人工评分' }}</button><button type="button" class="secondary" :disabled="revealing || saving" @click="reveal"><Eye :size="16" />{{ revealing ? '载入中…' : '查看排序对照' }}</button><span v-if="message" role="status" class="caption">{{ message }}</span></div>
      </form>
    </section>

    <section v-if="comparison" class="panel task-panel">
      <h2>顺序对照</h2><p class="caption mt-2">{{ comparison.home_order_source === 'explicit' ? '首页原位来自运行中保存的顺序。' : '首页原位由历史报告中的合格候选顺序还原。' }}</p>
      <div class="table-wrap mt-4"><table><thead><tr><th>候选</th><th>首页</th><th>重排</th><th>命中词</th></tr></thead><tbody>
        <tr v-for="row in tableRows" :key="row.candidate_id"><td><button v-if="row.target" type="button" class="text-link text-left" @click="reader = row.target">{{ row.title }}<BookOpen :size="14" class="shrink-0" /></button><template v-else>{{ row.title }}</template></td><td>{{ row.home_rank }}</td><td><span class="rank-pill">{{ row.focus_rank }}<ArrowUpRight v-if="row.focus_rank < row.home_rank" :size="14" /><ArrowDownRight v-else-if="row.focus_rank > row.home_rank" :size="14" class="text-slate-400" /></span></td><td>{{ row.matched_terms.join(' / ') || '—' }}</td></tr>
      </tbody></table></div>
    </section>
    <ArticleReaderDialog v-model="reader" />
  </div>
</template>
