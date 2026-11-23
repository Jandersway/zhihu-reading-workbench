import tailwindcss from '@tailwindcss/vite'

export default defineNuxtConfig({
  compatibilityDate: '2026-11-01',
  devtools: { enabled: false },
  css: ['~/assets/main.css'],
  vite: { plugins: [tailwindcss()] },
  runtimeConfig: { agentApi: 'http://127.0.0.1:8765' },
  app: {
    head: {
      title: '知乎 Agent · 阅读工作台',
      htmlAttrs: { lang: 'zh-CN' },
      meta: [{ name: 'description', content: '知乎二次推荐、证据审计与人工盲评。' }],
    },
  },
})
