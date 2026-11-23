<script setup lang="ts">
import { Search } from 'lucide-vue-next'

const query = defineModel<string>({ required: true })
const props = defineProps<{ busy?: boolean; disabled?: boolean }>()
const emit = defineEmits<{ search: [query: string] }>()
const input = ref<HTMLInputElement | null>(null)
const inputId = useId()

function submit() {
  const keyword = query.value.trim()
  if (keyword && !props.busy && !props.disabled) emit('search', keyword)
}

defineExpose({ focus: () => input.value?.focus() })
</script>

<template>
  <form class="article-search" role="search" aria-label="搜索知乎文章" @submit.prevent="submit">
    <label class="sr-only" :for="inputId">搜索知乎文章</label>
    <Search :size="20" aria-hidden="true" class="article-search-icon" />
    <input :id="inputId" ref="input" v-model="query" type="search" maxlength="100"
      placeholder="输入主题或关键词" enterkeyhint="search" />
    <button type="submit" class="primary" :disabled="busy || disabled || !query.trim()">
      {{ busy ? '搜索中…' : '搜索知乎' }}
    </button>
  </form>
</template>

<style scoped>
.article-search {
  display: flex; align-items: center; gap: 10px; min-width: 0;
  padding: 5px 5px 5px 15px; border: 1px solid var(--line-strong);
  border-radius: 12px; background: #fff; color: var(--muted);
}
.article-search:focus-within { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
.article-search-icon { flex: none; }
.article-search input { flex: 1; min-width: 0; min-height: 43px; outline: none; color: var(--ink); font-size: 15px; }
.article-search input::placeholder { color: var(--muted); }
.article-search button { flex: none; min-height: 42px; white-space: nowrap; }
@media (max-width: 420px) {
  .article-search { gap: 7px; padding-left: 10px; }
  .article-search button { padding-inline: 10px; }
}
</style>
