<script setup lang="ts">
import { ExternalLink } from 'lucide-vue-next'
import type { Task } from '~/types/agent'
defineProps<{ task: Extract<Task, { kind: 'research' }> }>()
</script>

<template>
  <section class="panel task-panel">
    <div class="flex flex-wrap items-center justify-between gap-3"><h2>资料问答结果</h2><span class="status" :class="task.status" role="status">{{ statusLabels[task.status] }}</span></div>
    <p class="caption mt-2">{{ task.mode === 'live' ? '模型问答' : task.research_scope === 'library' ? '离线原文摘录' : '离线演示' }} · {{ task.focus }}</p>
    <p v-if="task.error" class="notice error mt-4" role="alert">{{ task.error }}</p>
    <div v-if="task.result" class="mt-4 space-y-4">
      <p class="notice whitespace-pre-wrap">{{ task.result.answer }}</p>
      <p v-if="task.result.citation_warning" class="notice error">输出的引用缺失或包含无法核对的编号。</p>
      <div v-if="task.result.sources.length" class="subtle-box"><h3>已读来源</h3>
        <ul class="mt-2 space-y-3"><li v-for="source in task.result.sources" :key="source.citation_id">
          <strong>[{{ source.citation_id }}] {{ source.title }}</strong>
          <NuxtLink v-if="source.library_item_id" :to="{ path: '/library', query: { item: source.library_item_id } }" class="text-link ml-2">打开阅读库原文</NuxtLink>
          <NuxtLink v-else-if="task.mode === 'demo' || zhihuUrl(source.url)" :to="{ path: '/library', query: task.mode === 'demo' ? { sample: '1' } : { url: source.url } }" class="text-link ml-2">加入阅读库</NuxtLink>
          <a v-if="zhihuUrl(source.url) && (source.library_item_id || task.mode === 'live')" :href="zhihuUrl(source.url)" target="_blank" rel="noopener noreferrer" class="text-link ml-2"><ExternalLink :size="13" />知乎原文</a>
          <blockquote v-for="quote in source.quotes" :key="quote" class="quote-box mt-2 whitespace-pre-wrap">{{ quote }}</blockquote>
        </li></ul>
      </div>
      <details v-if="task.result.trace.length" class="subtle-box"><summary>检索与阅读记录</summary><ol class="mt-3 space-y-2"><li v-for="(row, index) in task.result.trace" :key="index" class="caption">第 {{ row.round }} 轮 · {{ row.tool }} · {{ row.ok ? '成功' : '失败' }} · {{ row.elapsed_ms }} ms</li></ol></details>
      <SaveResearchNote v-if="task.result.completed && task.result.sources.length" :key="task.id" :question="task.focus" :result="task.result" />
      <p v-if="task.result.skill" class="caption break-all">Skill {{ task.result.skill.name }} · {{ task.result.skill.sha256 }}</p>
    </div>
  </section>
</template>
