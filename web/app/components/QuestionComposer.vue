<script setup lang="ts">
import { ArrowUp, Sparkles } from 'lucide-vue-next'
import type { QuestionSuggestion } from '~/composables/useQuestionSuggestions'

const question = defineModel<string>({ required: true })
defineProps<{
  suggestions: QuestionSuggestion[]
  suggesting?: boolean
  blocked?: boolean
  busy?: boolean
  submitLabel?: string
  busyLabel?: string
}>()
const emit = defineEmits<{ submit: [] }>()
const inputId = useId()
const editor = ref<HTMLTextAreaElement | null>(null)

function pick(text: string) {
  question.value = text
  editor.value?.focus()
}
</script>

<template>
  <form class="question-composer" @submit.prevent="emit('submit')">
    <div v-if="suggestions.length || suggesting" class="question-suggestions" aria-label="问题建议">
      <p v-if="suggesting" class="caption" role="status">正在从资料生成问题…</p>
      <template v-else>
        <p class="caption"><Sparkles :size="14" aria-hidden="true" />从资料生成的问题</p>
        <div class="question-chips">
          <button v-for="(item, index) in suggestions" :key="index" type="button" :disabled="busy"
            :title="item.source_title" @click="pick(item.text)">{{ item.text }}</button>
        </div>
      </template>
    </div>
    <div class="question-box">
      <label :for="inputId" class="sr-only">你的问题</label>
      <textarea :id="inputId" ref="editor" v-model="question" rows="2" maxlength="300"
        placeholder="输入你想问的问题" :disabled="busy" />
      <div class="question-actions">
        <div class="question-options"><slot name="options" /></div>
        <button type="submit" class="primary" :disabled="busy || blocked || !question.trim()">
          {{ busy ? (busyLabel || '正在查找…') : (submitLabel || '提问') }} <ArrowUp :size="15" aria-hidden="true" />
        </button>
      </div>
    </div>
    <slot />
  </form>
</template>

<style scoped>
.question-composer { display: grid; gap: 11px; min-width: 0; }
.question-suggestions { display: grid; gap: 7px; }
.question-suggestions > p { display: flex; align-items: center; gap: 6px; color: var(--accent); }
.question-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.question-chips button { min-width: 0; padding: 5px 9px; background: var(--wash); border: 1px solid transparent; border-radius: 9px; color: var(--ink); text-align: left; font-size: 13px; overflow-wrap: anywhere; }
.question-chips button:hover:not(:disabled) { border-color: var(--line-strong); background: var(--accent-soft); }
.question-box { min-width: 0; background: white; border: 1px solid var(--line-strong); border-radius: 13px; padding: 8px 9px; }
.question-box:focus-within { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-soft); }
textarea { display: block; width: 100%; min-height: 64px; padding: 6px 8px; resize: vertical; outline: none; }
textarea::placeholder { color: var(--muted); }
.question-actions, .question-options { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.question-actions { justify-content: space-between; }
.question-actions > button { min-height: 36px; margin-left: auto; padding: 5px 12px; }
</style>
