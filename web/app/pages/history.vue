<script setup lang="ts">
import { ArrowRight, History } from 'lucide-vue-next'
import type { TaskSummary } from '~/types/agent'

const rows = ref<TaskSummary[]>([])
const error = ref('')
const loading = ref(true)
function taskLabel(row: TaskSummary) {
  if (row.kind === 'research') return row.mode === 'demo' ? '资料问答 · 本地运行' : '资料问答 · 实时模型'
  return row.mode === 'demo' ? '推荐演示' : '实时推荐报告'
}
async function load() {
  loading.value = true
  error.value = ''
  try { rows.value = await agentApi<TaskSummary[]>('/tasks') }
  catch (reason) { error.value = errorText(reason) }
  finally { loading.value = false }
}
onMounted(load)
</script>

<template>
  <div class="page-stack">
    <div class="page-heading"><span class="tile-icon"><History :size="23" /></span><h1>运行记录</h1><p>查看最近 30 次网页任务，重新打开保存的结果和执行进度。</p></div>
    <section class="panel task-panel">
      <div class="flex items-center justify-between gap-3"><h2>任务列表</h2><button class="secondary" :disabled="loading" @click="load">刷新</button></div>
      <p v-if="loading" class="notice mt-4" role="status">正在读取…</p><p v-else-if="error" class="notice error mt-4" role="alert">{{ error }}</p>
      <div v-else-if="!rows.length" class="notice mt-4">还没有问答记录。<NuxtLink to="/?task=ask" class="text-link">去提问</NuxtLink></div>
      <div v-else class="mt-3 history-list"><NuxtLink v-for="row in rows" :key="row.id" :to="{ path: '/', query: { run: row.id, task: row.kind === 'recommend' ? 'recommend' : 'ask' } }" class="history-row"><div><strong>{{ row.focus }}</strong><p class="caption mt-1">{{ formatTime(row.created_at) }} · {{ taskLabel(row) }}</p></div><span class="status" :class="row.status">{{ statusLabels[row.status] }}</span><ArrowRight :size="17" /></NuxtLink></div>
    </section>
  </div>
</template>
