<script setup lang="ts">
import { ArrowRight, BookOpenText, ClipboardList, Compass, LibraryBig, LogOut, MessageCircleQuestion, PanelLeft, Settings, X } from 'lucide-vue-next'
import { createDiscoveryFeed, discoveryFeedKey } from '~/composables/useDiscoveryFeed'
import { createReaderConversations, readerConversationsKey } from '~/composables/useReaderConversations'
import { cookieFromCurlCopy, isCurlCopy } from '~/utils/zhihuCookiePaste'

type User = { name: string; account: string }
const menu = ref(false)
const route = useRoute()
const user = ref<User | null>(null)
const readerConversations = createReaderConversations()
provide(readerConversationsKey, readerConversations)
const discoveryFeed = createDiscoveryFeed()
provide(discoveryFeedKey, discoveryFeed)
const ready = ref(false)
const cookieInput = ref('')
const cookieImported = ref(false)
const busy = ref(false)
const error = ref('')
let changes: BroadcastChannel | undefined
let sessionTimer: ReturnType<typeof setInterval> | undefined
let revision = 0

function setUser(value: User | null) {
  readerConversations.setAccount(value?.account ?? null)
  discoveryFeed.setAccount(value?.account ?? null)
  user.value = value
}

async function refreshSession() {
  const request = ++revision
  try {
    const data = await agentApi<{ user: User | null }>('/auth/session')
    if (request !== revision) return
    setUser(data.user)
    ready.value = true
    error.value = ''
  } catch (reason) {
    if (request === revision && !ready.value) error.value = errorText(reason)
  }
}

async function login() {
  if (busy.value || !cookieInput.value.trim()) return
  if (isCurlCopy(cookieInput.value) && !importCurl(cookieInput.value)) return
  if (cookieInput.value.length > 16384) return
  revision++
  busy.value = true
  error.value = ''
  const cookie = cookieInput.value
  try {
    const data = await agentApi<{ user: User }>('/auth/login', { method: 'POST', body: { cookie } })
    cookieInput.value = ''
    cookieImported.value = false
    setUser(data.user)
    changes?.postMessage('session')
  } catch (reason) {
    error.value = errorText(reason)
  } finally { busy.value = false }
}

function importCurl(text: string) {
  const result = cookieFromCurlCopy(text)
  cookieInput.value = result.cookie ?? ''
  cookieImported.value = Boolean(result.cookie)
  error.value = result.error ?? ''
  return cookieImported.value
}

function pasteCookie(event: ClipboardEvent) {
  const input = event.currentTarget as HTMLInputElement
  const pasted = event.clipboardData?.getData('text/plain')
  if (!pasted) return
  if (isCurlCopy(pasted)) {
    event.preventDefault()
    importCurl(pasted)
    return
  }
  if (!/[\r\n]/.test(pasted)) return
  // Password inputs discard line breaks. Retain the pasted header for server validation.
  event.preventDefault()
  cookieInput.value = input.value.slice(0, input.selectionStart ?? 0) + pasted
    + input.value.slice(input.selectionEnd ?? input.value.length)
  cookieImported.value = false
  error.value = ''
}

async function logout() {
  if (busy.value) return
  revision++
  busy.value = true
  error.value = ''
  try {
    await agentApi('/auth/logout', { method: 'POST' })
    setUser(null)
    menu.value = false
    changes?.postMessage('session')
    await navigateTo('/', { replace: true })
  } catch (reason) {
    error.value = errorText(reason)
  } finally { busy.value = false }
}

function checkSession() {
  if (!busy.value) void refreshSession()
}

onMounted(() => {
  void refreshSession()
  if ('BroadcastChannel' in window) {
    changes = new BroadcastChannel('zhihu-workbench-auth')
    changes.onmessage = checkSession
  }
  window.addEventListener('focus', checkSession)
  window.addEventListener('zhihu-session-check', checkSession)
  sessionTimer = setInterval(() => {
    if (user.value && document.visibilityState === 'visible') checkSession()
  }, 60 * 60 * 1000)
})
onBeforeUnmount(() => {
  changes?.close()
  if (sessionTimer) clearInterval(sessionTimer)
  window.removeEventListener('focus', checkSession)
  window.removeEventListener('zhihu-session-check', checkSession)
})

const nav = [
  { to: '/', title: '工作台', icon: BookOpenText },
  { to: '/discover', title: '发现文章', icon: Compass },
  { to: '/library', title: '我的阅读库', icon: LibraryBig },
  { to: '/?task=ask', title: '资料问答', icon: MessageCircleQuestion },
  { to: '/history', title: '运行记录', icon: ClipboardList },
  { to: '/settings', title: '模型设置', icon: Settings },
]
const active = computed(() => {
  if (route.path === '/agent-rl') return 'Agentic RL 实验'
  if (route.path === '/lab') return '排序评测'
  if (route.path === '/') {
    if (route.query.run) return route.query.task === 'ask' ? '资料问答' : '运行记录'
    if (route.query.article || route.query.task === 'ask') return '资料问答'
  }
  return nav.find(item => item.to === route.path)?.title ?? '工作台'
})
watch(() => route.fullPath, () => { menu.value = false })
</script>

<template>
  <div v-if="!user" class="login-screen">
    <div class="login-layout">
      <div class="login-intro">
        <div class="login-brand"><span class="brand-mark"><BookOpenText :size="21" /></span>知乎 Agent</div>
        <p class="login-promise">找到值得读的文章，<br />把原文和笔记留在一起。</p>
        <p class="login-description">搜索知乎、保存资料、对着原文提问。</p>
      </div>
      <section class="task-panel login-card" aria-labelledby="login-title">
        <h1 id="login-title">登录知乎阅读工作台</h1>
        <p v-if="!ready && !error" class="caption mt-3" role="status">正在检查登录状态…</p>
        <form v-if="ready" class="login-form" @submit.prevent="login">
          <label for="login-cookie" class="field-label">知乎 Cookie</label>
          <input id="login-cookie" v-model="cookieInput" type="password" class="field min-w-0"
            autocomplete="off" spellcheck="false" placeholder="粘贴 cURL（cmd）或 Cookie" :disabled="busy"
            aria-describedby="login-input-help" @paste="pasteCookie" @input="error = ''; cookieImported = false" />
          <p id="login-input-help" class="caption">可以粘贴「复制为 cURL（cmd）」的结果，也可以直接粘贴 Cookie 请求头的值。</p>
          <p v-if="cookieImported" class="caption" role="status">已从 cURL 提取 Cookie，可以点击登录。</p>
          <p v-if="cookieInput.length > 16384" class="notice error" role="alert">粘贴的内容太长。请只复制 Cookie 请求头的值。</p>
          <button class="primary" :disabled="busy || !cookieInput.trim() || cookieInput.length > 16384">{{ busy ? '正在验证账号…' : '登录' }} <ArrowRight :size="16" /></button>
          <details class="caption login-help">
            <summary>在哪里复制 Cookie？</summary>
            <ol class="list-decimal pl-5 mt-2 space-y-1">
              <li>在知乎官网登录自己的账号。</li>
              <li>按 F12 打开「网络」，刷新知乎页面。</li>
              <li>右键一条发往 www.zhihu.com 的请求，选择「复制 → 复制为 cURL（cmd）」，再粘贴到上方。</li>
            </ol>
          </details>
        </form>
        <p v-if="error" class="notice error mt-3" role="alert">{{ error }}</p>
        <button v-if="!ready && error" type="button" class="secondary mt-3" @click="refreshSession">重试</button>
      </section>
    </div>
  </div>
  <div v-else :key="user.account" class="shell">
    <aside class="sidebar" :class="{ 'sidebar-open': menu }">
      <NuxtLink to="/" class="brand" @click="menu = false">
        <span class="brand-mark"><BookOpenText :size="21" /></span>
        <span>知乎 Agent</span>
      </NuxtLink>
      <nav aria-label="工作台导航">
        <template v-for="item in nav" :key="item.to">
          <hr v-if="item.to === '/history'" class="nav-divider" />
          <NuxtLink :to="item.to" exact-active-class="" class="nav-item" :class="{ 'nav-active': item.title === active }"
            :aria-current="item.title === active ? 'page' : undefined">
            <component :is="item.icon" :size="18" />{{ item.title }}
          </NuxtLink>
        </template>
      </nav>
    </aside>
    <button v-if="menu" class="scrim" aria-label="关闭菜单" @click="menu = false"></button>
    <div class="surface">
      <header class="topbar">
        <button class="icon-button mobile-menu" :aria-expanded="menu" aria-label="切换导航" @click="menu = !menu">
          <X v-if="menu" :size="20" /><PanelLeft v-else :size="20" />
        </button>
        <span>{{ active }}</span>
        <div class="account-bar"><span class="caption account-name">{{ user.name }}</span><button type="button" class="secondary" :disabled="busy" @click="logout"><LogOut :size="15" />退出</button></div>
      </header>
      <main class="content"><p v-if="error" class="notice error mb-4" role="alert">{{ error }}</p><NuxtPage :key="user.account" /></main>
    </div>
  </div>
</template>

<style scoped>
.login-screen { min-height: 100dvh; display: grid; place-items: center; padding: clamp(15px, 5vw, 48px); }
.login-layout { width: min(100%, 930px); display: grid; grid-template-columns: 1.13fr 1fr; border: 1px solid var(--line); border-radius: 20px; overflow: hidden; box-shadow: 0 18px 60px #1d302419; }
.login-intro { padding: clamp(24px, 5vw, 54px); background: #153c30; color: #fff; }
.login-brand { display: flex; align-items: center; gap: 10px; font-weight: 700; }
.login-brand .brand-mark { background: #d7ebdd; color: #153c30; }
.login-promise { margin-top: clamp(26px, 6vw, 68px); font-size: clamp(25px, 4vw, 41px); font-weight: 760; line-height: 1.35; letter-spacing: -.027em; }
.login-description { margin-top: 12px; color: #d2e5d9; line-height: 1.65; }
.login-card { background: white; padding: clamp(24px, 5vw, 49px); }
.login-card h1 { font-size: clamp(22px, 3vw, 29px); font-weight: 760; }
.login-form { display: grid; gap: 11px; margin-top: 20px; }
.login-help { margin-top: 3px; }
.login-help summary { color: var(--accent); font-weight: 690; }
.login-help li { overflow-wrap: anywhere; }
.account-bar { margin-left: auto; min-width: 0; display: flex; gap: 8px; align-items: center; }
.account-bar button { min-height: 36px; padding: 5px 10px; white-space: nowrap; }
.account-name { max-width: 140px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@media (max-width: 730px) { .login-layout { grid-template-columns: minmax(0, 1fr); max-width: 475px; } }
</style>
