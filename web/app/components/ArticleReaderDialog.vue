<script setup lang="ts">
import { X } from 'lucide-vue-next'
import type { ReaderTarget } from '~/types/agent'

const target = defineModel<ReaderTarget | null>({ required: true })
const dialog = ref<HTMLDialogElement | null>(null)
const scrollArea = ref<HTMLElement | null>(null)
let trigger: HTMLElement | null = null

watch(target, (value) => {
  const element = dialog.value
  if (!element) return
  if (value && !element.open) {
    trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null
    if (scrollArea.value) scrollArea.value.scrollTop = 0
    element.showModal()
  } else if (!value && element.open) element.close()
}, { flush: 'post' })

function closed() {
  target.value = null
  trigger?.focus({ preventScroll: true })
  trigger = null
}
</script>

<template>
  <dialog ref="dialog" aria-label="文章阅读面板" class="reader-dialog" @close="closed" @click.self="target = null">
    <div class="reader-window">
      <header><strong>文章阅读</strong><button type="button" class="secondary" @click="target = null"><X :size="16" />关闭阅读</button></header>
      <div ref="scrollArea" class="reader-scroll"><ArticleReader v-if="target" :target="target" /></div>
    </div>
  </dialog>
</template>

<style scoped>
.reader-dialog { position: fixed; inset: 0 0 0 auto; margin: 0; width: min(760px, 100vw); max-width: 100vw; height: 100dvh; max-height: 100dvh; background: #fff; color: #182436; box-shadow: -12px 0 40px #0e20352b; }
.reader-dialog::backdrop { background: #10203970; }
.reader-window { height: 100%; display: grid; grid-template-rows: auto minmax(0, 1fr); }
.reader-window > header { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 11px clamp(15px, 4vw, 32px); border-bottom: 1px solid #dce4ee; }
.reader-scroll { overflow: auto; overscroll-behavior: contain; padding: clamp(15px, 4vw, 32px); }
</style>
