<script setup lang="ts">
import MarkdownIt from 'markdown-it'

const props = defineProps<{ body: string }>()
const markdown = new MarkdownIt({ html: false, breaks: true })
markdown.renderer.rules.image = (tokens, index, options, env, renderer) => {
  const token = tokens[index]
  if (!token) return ''
  const src = String(token.attrGet('src') ?? '')
  try {
    const url = new URL(src.startsWith('//') ? `https:${src}` : src)
    if (url.protocol === 'https:' && /^pic[a-z0-9]*\.zhimg\.com$/.test(url.host) && !url.username && !url.password) {
      token.attrSet('src', url.href)
      token.attrSet('loading', 'lazy')
      token.attrSet('decoding', 'async')
      token.attrSet('referrerpolicy', 'no-referrer')
      token.attrSet('alt', renderer.renderInlineAsText(token.children ?? [], options, env))
      return renderer.renderToken(tokens, index, options)
        + '<span class="article-image-fallback" hidden>图片暂时加载失败，可尝试刷新或查看知乎原文。</span>'
    }
  } catch { /* Keep a text placeholder for malformed or relative URLs. */ }
  const alt = token.content
  return markdown.utils.escapeHtml(alt ? `［图片：${alt}］` : '［图片］')
}
const rendered = computed(() => markdown.render(props.body))

function onImageError(event: Event) {
  const img = event.target
  if (!(img instanceof HTMLImageElement)) return
  img.hidden = true
  img.nextElementSibling?.removeAttribute('hidden')
}
</script>

<template>
  <div class="article-body" v-html="rendered" @error.capture="onImageError"></div>
</template>

<style scoped>
.article-body { max-width: 78ch; line-height: 1.8; overflow-wrap: anywhere; }
.article-body :deep(p), .article-body :deep(blockquote), .article-body :deep(pre),
.article-body :deep(ul), .article-body :deep(ol) { margin: .85em 0; }
.article-body :deep(h1), .article-body :deep(h2), .article-body :deep(h3),
.article-body :deep(h4), .article-body :deep(h5), .article-body :deep(h6) { font-weight: 700; font-size: 1.2em; margin: 1.4em 0 .6em; }
.article-body :deep(ul), .article-body :deep(ol) { padding-inline-start: 1.6em; }
.article-body :deep(ul) { list-style: disc; }
.article-body :deep(ol) { list-style: decimal; }
.article-body :deep(blockquote) { border-left: 3px solid #afc4de; padding-inline-start: 1em; color: #53647b; }
.article-body :deep(pre) { overflow: auto; background: #eef4fb; padding: 12px; border-radius: 6px; }
.article-body :deep(a) { color: #245bb8; text-decoration: underline; }
.article-body :deep(table) { display: block; overflow: auto; }
.article-body :deep(img) { display: block; max-width: 100%; height: auto; margin: 1em auto; border-radius: 6px; }
.article-body :deep(img[hidden]) { display: none; }
.article-body :deep(.article-image-fallback) { color: #53647b; font-size: .92em; }
</style>
