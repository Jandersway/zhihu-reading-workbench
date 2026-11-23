export interface QuestionSuggestion {
  text: string
  source_title: string
}

interface QuestionScope {
  item_id?: string
  preview_id?: string
}

export function useQuestionSuggestions(scope: () => QuestionScope | null) {
  const suggestions = ref<QuestionSuggestion[]>([])
  const suggesting = ref(false)
  const query = computed(() => {
    const selected = scope()
    if (!selected) return null
    if (selected.item_id) return new URLSearchParams({ item_id: selected.item_id }).toString()
    if (selected.preview_id) return new URLSearchParams({ preview_id: selected.preview_id }).toString()
    return ''
  })

  watch(query, async (params, _previous, onCleanup) => {
    let current = true
    onCleanup(() => { current = false })
    suggestions.value = []
    suggesting.value = params !== null
    if (params === null) return
    try {
      const result = await agentApi<{ questions: QuestionSuggestion[] }>('/library/questions' + (params ? '?' + params : ''))
      if (current) suggestions.value = result.questions
    } catch {
      // Typing and submitting a question remain available when suggestions cannot load.
    } finally {
      if (current) suggesting.value = false
    }
  }, { immediate: true })

  return { suggestions, suggesting }
}
