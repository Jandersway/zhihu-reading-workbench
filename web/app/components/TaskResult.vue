<script setup lang="ts">
import { ArrowDown, ArrowUp, BookMarked, Check, ExternalLink, FileText, GitBranch, ListOrdered } from 'lucide-vue-next'
import type { Task } from '~/types/agent'

const props = defineProps<{ task: Extract<Task, { kind: 'recommend' }> }>()
const reached = computed(() => new Set(props.task.events.map(event => event.stage)))
const result = computed(() => props.task.result)
const tab = ref<'recommend' | 'ranking' | 'audit'>('recommend')
watch(() => props.task.id, () => { tab.value = 'recommend' })
const scores = computed(() => result.value?.candidate_ranking.items ?? [])
const titles = computed(() => new Map(result.value?.candidates.map(row => [row.candidate_id, row.title]) ?? []))
const kind: Record<string, string> = { Recommendation: '推荐', DocumentSnapshot: '文档快照', Passage: '正文引文', Claim: '主张', FavoriteExcerpt: '收藏片段' }
const relations: Record<string, string> = { HAS_PASSAGE: '包含片段', HAS_CLAIM: '包含主张', CITES: '引用', PREFERENCE_CLUE: '偏好线索' }
const graphNodes = computed(() => new Map(result.value?.graph?.nodes.map(node => [node.id, node]) ?? []))
const graphLabel = (id: string) => kind[graphNodes.value.get(id)?.type ?? ''] ?? '证据'
const tabs = [{ id: 'recommend', label: '推荐与证据', icon: BookMarked }, { id: 'ranking', label: '候选排名', icon: ListOrdered }, { id: 'audit', label: '运行审计', icon: GitBranch }] as const
</script>

<template>
  <section class="panel task-panel">
    <div class="flex flex-wrap items-center justify-between gap-3"><h2>执行进度</h2><span class="status" :class="task.status" role="status">{{ statusLabels[task.status] }}</span></div>
    <p class="caption mt-2 break-words">{{ task.mode === 'demo' ? '离线演示' : '实时推荐' }} · {{ task.focus }}</p>
    <ol class="stage-list mt-5">
      <li v-for="([key, label], index) in stages" :key="key" :class="{ reached: reached.has(key) }"><span><Check v-if="reached.has(key)" :size="15" /><template v-else>{{ index + 1 }}</template></span>{{ label }}</li>
    </ol>
    <p v-if="task.error" class="notice error mt-4" role="alert">{{ task.error }}</p>
  </section>

  <section v-if="result" class="panel task-panel">
    <div class="view-tabs" role="group" aria-label="运行结果视图">
      <button v-for="item in tabs" :key="item.id" :aria-pressed="tab === item.id" @click="tab = item.id"><component :is="item.icon" :size="17" />{{ item.label }}</button>
    </div>

    <div v-if="tab === 'recommend'" class="mt-5 space-y-5">
      <p v-if="!result.recommendations.length" class="notice">{{ result.reason || '本轮没有发布推荐。' }}</p>
      <article v-for="item in result.recommendations" :key="item.candidate_id" class="recommend-card">
        <div class="flex flex-wrap justify-between items-start gap-3"><h3>{{ item.title }}</h3><div class="flex flex-wrap gap-3"><NuxtLink :to="{ path: '/library', query: task.mode === 'demo' ? { sample: '1' } : { url: item.url } }" class="text-link">加入阅读库</NuxtLink><a v-if="task.mode === 'live' && zhihuUrl(item.url)" :href="zhihuUrl(item.url)" target="_blank" rel="noopener noreferrer" class="text-link">知乎原文 <ExternalLink :size="12" /></a></div></div>
        <p class="copy mt-2">{{ item.why }}</p>
        <dl class="detail-grid mt-4"><div><dt>阅读价值</dt><dd>{{ item.value }}</dd></div><div><dt>适用边界</dt><dd>{{ item.drawback }}</dd></div></dl>
        <div class="detail-grid mt-4">
          <div class="quote-box"><h4><FileText :size="15" />正文引文</h4><blockquote>{{ item.evidence_quote }}</blockquote></div>
          <div class="quote-box"><h4><BookMarked :size="15" />收藏线索</h4><blockquote>{{ item.preference_quote }}</blockquote><p class="caption mt-2">{{ item.preference_reason }}</p></div>
        </div>
      </article>
      <details v-if="result.graph" class="subtle-box">
        <summary>查看证据图关系 · {{ result.graph.nodes.length }} 个节点 / {{ result.graph.edges.length }} 条边</summary>
        <p class="caption mt-3">引用边记录主张引用了哪段文本。事实支持由评审判断，收藏边记录兴趣线索。</p>
        <ul class="edge-list mt-3"><li v-for="edge in result.graph.edges" :key="`${edge.source}:${edge.target}:${edge.relation}`"><span>{{ graphLabel(edge.source) }}</span><span class="edge-relation">{{ relations[edge.relation] }} →</span><span>{{ graphLabel(edge.target) }}</span></li></ul>
        <div v-for="node in result.graph.nodes.filter(node => node.type === 'Passage')" :key="node.id" class="caption mt-3 break-all">正文偏移 {{ node.start }}–{{ node.end }} · SHA256 {{ node.source_sha256 }}</div>
      </details>
    </div>

    <div v-else-if="tab === 'ranking'" class="mt-5">
      <p class="caption mb-4">排序分来自目标词在标题和摘要的覆盖情况，提供给规划阶段参考。</p>
      <div v-if="scores.length" class="table-wrap"><table><thead><tr><th>候选</th><th>首页</th><th>重排</th><th>命中词</th></tr></thead><tbody>
        <tr v-for="score in scores" :key="score.candidate_id"><td>{{ titles.get(score.candidate_id) }}</td><td>{{ score.home_rank }}</td><td><span class="rank-pill">{{ score.focus_rank }} <ArrowUp v-if="score.focus_rank < score.home_rank" :size="13" /><ArrowDown v-else-if="score.focus_rank > score.home_rank" :size="13" class="text-slate-400" /></span></td><td>{{ score.matched_terms.join(' / ') || '—' }}</td></tr>
      </tbody></table></div><p v-else class="notice">本轮没有产生候选重排。</p>
    </div>

    <div v-else class="mt-5 space-y-5">
      <div class="detail-grid"><div class="subtle-box"><strong>{{ (result.elapsed_ms / 1000).toFixed(2) }} s</strong><p class="caption">工作流耗时</p></div><div class="subtle-box"><strong>{{ result.usage_complete ? result.usage_totals.total_tokens.toLocaleString() : '未知' }}</strong><p class="caption">模型 Tokens</p></div></div>
      <div class="table-wrap"><table><thead><tr><th>阶段</th><th>模型调用</th><th>Tokens</th><th>模型 ms</th><th>工具 ms</th></tr></thead><tbody>
        <tr v-for="([key, label]) in stages" :key="key"><td>{{ label }}</td><td>{{ result.cost_breakdown.by_stage[key]?.model.calls ?? '—' }}</td><td>{{ result.cost_breakdown.by_stage[key]?.model.total_tokens ?? '未知' }}</td><td>{{ result.cost_breakdown.by_stage[key]?.model.elapsed_ms ?? '—' }}</td><td>{{ result.cost_breakdown.by_stage[key]?.tools.elapsed_ms ?? '—' }}</td></tr>
      </tbody></table></div>
      <div v-if="result.reviews.length"><h3>逐条评审</h3><ul class="space-y-2 mt-2"><li v-for="(review, index) in result.reviews" :key="index" class="subtle-box"><strong>{{ review.approved ? '通过' : '退回' }}</strong><p v-for="issue in review.issues" :key="issue" class="caption mt-2">{{ issue }}</p><p v-for="check in review.checks" :key="check.evidence_id" class="caption mt-2">{{ check.explanation }} · {{ check.evidence_id.slice(0, 12) }}</p></li></ul></div>
      <details class="subtle-box"><summary>状态流转</summary><ul class="space-y-2 mt-3"><li v-for="event in task.events" :key="event.seq" class="caption"><strong>{{ event.stage }}</strong> · {{ event.detail }}</li></ul></details>
      <p v-if="result.skill" class="caption break-all">Skill {{ result.skill.name }} · {{ result.skill.sha256 }}</p>
    </div>
  </section>
</template>
