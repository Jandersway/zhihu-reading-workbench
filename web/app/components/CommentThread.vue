<script setup lang="ts">
import MarkdownIt from 'markdown-it'
import type { LibraryComment } from '~/types/agent'
import { commentLink } from '~/utils/commentLink'

const props = defineProps<{ rows: LibraryComment[] }>()
const emit = defineEmits<{ openArticle: [url: string]; openImage: [url: string] }>()
const markdown = new MarkdownIt({ html: false, breaks: true, linkify: true })
markdown.renderer.rules.image = (tokens, index) => markdown.utils.escapeHtml(tokens[index]?.content ?? '')
markdown.renderer.rules.link_open = (tokens, index, options, _env, renderer) => {
  const token = tokens[index]
  if (!token) return ''
  const href = token.attrGet('href')
  const link = href ? commentLink(String(href)) : undefined
  if (!link) {
    token.attrs = null
  } else if (link.kind === 'search') {
    token.attrSet('href', `/discover?q=${encodeURIComponent(link.query)}`)
    token.attrSet('data-comment-search', '1')
  } else {
    token.attrSet('href', link.url)
    if (link.kind === 'external') {
      token.attrSet('target', '_blank')
      token.attrSet('rel', 'noopener noreferrer')
    } else {
      token.attrSet('data-comment-action', link.kind)
    }
  }
  return renderer.renderToken(tokens, index, options)
}
const rendered = computed(() => props.rows.map(row => ({ ...row, html: markdown.renderInline(row.content) })))

function follow(event: MouseEvent) {
  if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return
  const anchor = event.target instanceof Element ? event.target.closest('a[href]') : null
  if (!(anchor instanceof HTMLAnchorElement)) return
  if (anchor.dataset.commentSearch) {
    event.preventDefault()
    void navigateTo({ path: '/discover', query: { q: new URL(anchor.href).searchParams.get('q') } })
    return
  }
  if (!anchor.dataset.commentAction) return
  const link = commentLink(anchor.href)
  if (link?.kind === 'article' || link?.kind === 'image') {
    event.preventDefault()
    if (link.kind === 'article') emit('openArticle', link.url)
    else emit('openImage', link.url)
  }
}
</script>

<template>
  <ul class="space-y-3"><li v-for="row in rendered" :key="row.key" class="border-l border-slate-200 pl-3">
    <div class="flex flex-wrap items-center gap-x-2"><strong>{{ row.author }}</strong><span class="caption">{{ row.likes }} 赞</span></div>
    <p class="comment-copy copy mt-1 whitespace-pre-wrap break-words" v-html="row.html" @click="follow"></p>
    <div v-if="row.replies.length" class="mt-3"><CommentThread :rows="row.replies" @open-article="emit('openArticle', $event)" @open-image="emit('openImage', $event)" /></div>
  </li></ul>
</template>

<style scoped>
.comment-copy :deep(a[href]) { color: #245bb8; text-decoration: underline; text-underline-offset: 2px; }
</style>
