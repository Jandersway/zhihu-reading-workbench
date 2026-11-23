<script setup lang="ts">
import { KeyRound } from 'lucide-vue-next'
import type { ModelStatus } from '~/types/agent'

const config = ref<ModelStatus | null>(null)
const keyInput = ref('')
const providerId = ref('')
const modelId = ref('')
const provider = computed(() => config.value?.providers.find(option => option.id === providerId.value))
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const notice = ref('')

function selectProvider() {
  modelId.value = provider.value?.default_model ?? ''
  keyInput.value = ''
  notice.value = ''
}

function applyConfig(next: ModelStatus) {
  config.value = next
  providerId.value = next.provider ?? next.providers[0]?.id ?? ''
  modelId.value = next.model ?? provider.value?.default_model ?? ''
  keyInput.value = ''
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    applyConfig(await agentApi<ModelStatus>('/settings/model'))
  } catch (reason) { error.value = errorText(reason) }
  finally { loading.value = false }
}

async function update(remove = false) {
  const key = keyInput.value.trim()
  if (loading.value || saving.value || (!remove && (!key || !providerId.value || !modelId.value.trim()))) return
  saving.value = true
  error.value = ''
  notice.value = ''
  try {
    const next = remove
      ? await agentApi<ModelStatus>('/settings/model', { method: 'DELETE' })
      : await agentApi<ModelStatus>('/settings/model', {
        method: 'PUT', body: { provider: providerId.value, model: modelId.value.trim(), api_key: key },
      })
    applyConfig(next)
    notice.value = remove ? '已移除 API Key。' : 'API Key 已保存。'
  } catch (reason) { error.value = errorText(reason) }
  finally { saving.value = false }
}

onMounted(load)
</script>

<template>
  <div class="page-stack settings-page">
    <header class="page-heading">
      <h1>模型设置</h1>
      <p>填入你自己的 API Key，即可在资料问答中选择实时模型。</p>
    </header>
    <section class="panel task-panel" aria-labelledby="model-settings">
      <div class="flex items-center gap-3">
        <span class="tile-icon"><KeyRound :size="19" aria-hidden="true" /></span>
        <h2 id="model-settings">实时问答模型</h2>
        <span v-if="config" class="status" :class="{ completed: config.configured }">{{ config.configured ? '已配置' : '未配置' }}</span>
      </div>
      <p v-if="loading" class="caption mt-3" role="status">正在读取模型设置…</p>
      <form v-else-if="config" class="key-form" @submit.prevent="update()">
        <label for="model-provider" class="field-label">模型提供商</label>
        <select id="model-provider" v-model="providerId" class="field" :disabled="saving" @change="selectProvider">
          <option v-for="option in config.providers" :key="option.id" :value="option.id">{{ option.label }}</option>
        </select>
        <label for="model-id" class="field-label">模型 ID</label>
        <input id="model-id" v-model="modelId" type="text" class="field min-w-0" maxlength="160"
          autocomplete="off" spellcheck="false" :disabled="saving" placeholder="填写支持工具调用的模型 ID" required />
        <label for="model-api-key" class="field-label">API Key</label>
        <input id="model-api-key" v-model="keyInput" type="password" class="field min-w-0" maxlength="512"
          autocomplete="off" spellcheck="false" :disabled="saving" placeholder="粘贴你的 API Key" required />
        <p v-if="provider" class="caption">确认实时问答后，请求将发送到 {{ provider.host }}。</p>
        <p v-if="!config.live_allowed" class="notice">当前部署已关闭实时模型。</p>
        <div class="flex flex-wrap gap-2">
          <button type="submit" class="primary" :disabled="saving || !keyInput.trim() || !modelId.trim()">保存配置</button>
          <button v-if="config.configured" type="button" class="secondary" :disabled="saving" @click="update(true)">移除 API Key</button>
        </div>
      </form>
      <p v-if="error" class="notice error mt-3" role="alert">{{ error }}</p>
      <button v-if="!config && !loading" type="button" class="secondary mt-3" @click="load">重试</button>
      <p v-if="notice" class="caption mt-3" role="status">{{ notice }}</p>
    </section>
  </div>
</template>

<style scoped>
.settings-page { width: min(100%, 690px); }
.key-form { display: grid; gap: 8px; margin-top: 17px; }
</style>
