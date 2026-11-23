<script setup lang="ts">
import { FlaskConical } from 'lucide-vue-next'

interface Metrics {
  mean_reward: number; published: number; correct_abstentions: number
  missed: number; avg_reads: number; avg_verifications: number
}
interface Experiment {
  seed: number; training_episodes: number; training_cases: number; test_cases: number
  strategies: (Metrics & { name: string })[]
  seeds: (Metrics & { seed: number })[]
  reward: { publish: number; abstain: number; miss: number; read: number; verify: number }
  example: { topic: string; outcome: string; steps: { label: string; after: string[]; reward: number }[] }
}

const report = ref<Experiment | null>(null)
const error = ref('')
const statusNames: Record<string, string> = {
  unread: '未读', has_quote: '找到原文引用', missing_quote: '引用不在正文中',
  unavailable: '正文不可读', rejected: '证据不支持目标', verified: '校验通过',
}

async function load() {
  error.value = ''
  try { report.value = await agentApi<Experiment>('/lab/agent-rl') }
  catch (reason) { error.value = errorText(reason) }
}
onMounted(load)
</script>

<template>
  <div class="page-stack">
    <header class="page-heading">
      <NuxtLink to="/lab" class="text-link">返回排序评测</NuxtLink>
      <h1>Agentic RL 阅读策略实验</h1>
      <p>学习何时读另一篇、校验证据或停止。在合成环境中比较效果与工具成本。</p>
    </header>
    <p v-if="error" class="notice error" role="alert">{{ error }} <button class="text-link" @click="load">重试</button></p>
    <p v-else-if="!report" class="notice" role="status">正在运行离线实验…</p>
    <template v-if="report">
      <section class="panel task-panel" aria-labelledby="policy-results">
        <div class="flex items-center gap-3"><span class="tile-icon"><FlaskConical :size="19" /></span><h2 id="policy-results">策略对照</h2></div>
        <p class="caption mt-3">使用 {{ report.training_cases }} 个合成案例训练 {{ report.training_episodes }} 个回合。四种策略在同一组 {{ report.test_cases }} 个新主题案例上评估。</p>
        <div class="table-wrap mt-4">
          <table>
            <thead><tr><th>策略</th><th>平均奖励</th><th>通过后发布</th><th>正确拒答</th><th>漏掉可用证据</th><th>平均读取</th><th>平均校验</th></tr></thead>
            <tbody>
              <tr v-for="row in report.strategies" :key="row.name">
                <td><strong>{{ row.name }}</strong></td><td>{{ row.mean_reward.toFixed(3) }}</td><td>{{ row.published }}</td><td>{{ row.correct_abstentions }}</td><td>{{ row.missed }}</td><td>{{ row.avg_reads.toFixed(2) }}</td><td>{{ row.avg_verifications.toFixed(2) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="caption mt-3">奖励包含发布 {{ report.reward.publish }}、正确拒答 {{ report.reward.abstain }}、漏掉证据 {{ report.reward.miss }}，以及每次读取 {{ report.reward.read }}、校验 {{ report.reward.verify }}。随机种子 {{ report.seed }}。</p>
        <details class="subtle-box mt-4">
          <summary>不同随机种子的复测</summary>
          <ul class="mt-3 space-y-1">
            <li v-for="row in report.seeds" :key="row.seed" class="caption">种子 {{ row.seed }} · 平均奖励 {{ row.mean_reward.toFixed(3) }} · 平均读取 {{ row.avg_reads.toFixed(2) }} 次</li>
          </ul>
        </details>
      </section>
      <section class="panel task-panel" aria-labelledby="policy-trace">
        <h2 id="policy-trace">一次实际决策轨迹</h2>
        <p class="caption mt-2">主题 {{ report.example.topic }}。正文里的引文先做逐字检查，再用合成案例的语义标签校验是否支持目标。</p>
        <ol class="mt-4 space-y-2">
          <li v-for="(step, index) in report.example.steps" :key="index" class="subtle-box">
            <strong>{{ index + 1 }}. {{ step.label }}</strong>
            <p class="caption mt-1">候选 1 {{ statusNames[step.after[0] ?? ''] }} · 候选 2 {{ statusNames[step.after[1] ?? ''] }} · 本步奖励 {{ step.reward }}</p>
          </li>
        </ol>
      </section>
      <p class="caption">实验训练的是本地 Q 表，在线阅读继续使用现有策略。合成奖励衡量证据与工具成本，真实用户偏好由人工评测单独衡量。</p>
    </template>
  </div>
</template>
