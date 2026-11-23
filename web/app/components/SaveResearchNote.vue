<script setup lang="ts">
import type { LibraryItem, ResearchResult } from '~/types/agent'

const props = defineProps<{ question: string; result: ResearchResult; articleTitle?: string; articleUrl?: string | null }>()
const emit = defineEmits<{ saved: [item: LibraryItem] }>()
const opened = ref(false)
const title = ref('')
const body = ref('')
const saving = ref(false)
const saved = ref<LibraryItem | null>(null)
const error = ref('')
const sourceUrl = computed(() => zhihuUrl(props.articleUrl ?? undefined) || zhihuUrl(props.result.sources[0]?.url))
const titleId = useId()
const bodyId = useId()

function compose() {
  const subject = props.articleTitle || props.result.sources[0]?.title || props.question
  title.value = `阅读笔记 · ${subject}`.slice(0, 300)
  const references = props.result.sources.map(source => {
    const url = zhihuUrl(source.url)
    return `- [${source.citation_id}] ${source.title}${url ? '\n  ' + url : ''}`
  })
  body.value = [`## 问题\n\n${props.question}`, `## 回答\n\n${props.result.answer}`, '## 我的笔记',
    references.length ? `## 来源\n\n${references.join('\n')}` : ''].filter(Boolean).join('\n\n')
  opened.value = true
}

async function save() {
  if (!title.value.trim() || !body.value.trim() || saving.value) return
  saving.value = true
  error.value = ''
  try {
    saved.value = await agentApi<LibraryItem>('/library', {
      method: 'POST', body: { source: 'note', title: title.value, body: body.value, url: sourceUrl.value || null },
    })
    opened.value = false
    emit('saved', saved.value)
  } catch (reason) { error.value = errorText(reason) }
  finally { saving.value = false }
}
</script>

<template>
  <div class="space-y-2 note-actions">
    <p v-if="saved" class="caption" role="status">
      已保存到本地阅读库。
      <NuxtLink :to="{ path: '/library', query: { item: saved.id } }" class="text-link">打开笔记</NuxtLink>
    </p>
    <button v-else-if="!opened" type="button" class="secondary" @click="compose">存为笔记</button>
    <div v-else class="subtle-box space-y-2">
      <label :for="titleId" class="field-label">笔记标题</label>
      <input :id="titleId" v-model="title" class="field" maxlength="300" />
      <label :for="bodyId" class="field-label">笔记内容</label>
      <textarea :id="bodyId" v-model="body" class="field" rows="8" maxlength="300000" />
      <p class="caption">相近的文章在后续推荐中可参考这篇笔记。</p>
      <div class="flex flex-wrap gap-2">
        <button type="button" class="primary" :disabled="saving || !title.trim() || !body.trim()" @click="save">{{ saving ? '正在保存…' : '保存到本地' }}</button>
        <button type="button" class="secondary" :disabled="saving" @click="opened = false">取消</button>
      </div>
      <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    </div>
  </div>
</template>

<style scoped>
.note-actions .field { width: 100%; }
</style>
