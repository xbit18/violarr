<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../api/client'
import type { ApplicationUpdate } from '../api/types'
import { useLocale } from '../i18n'

const locale = useLocale()
const update = ref<ApplicationUpdate | null>(null)
const stable =
  /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/
let timer: ReturnType<typeof setTimeout> | undefined
let timeout: ReturnType<typeof setTimeout> | undefined
let controller: AbortController | undefined
let disposed = false
let earlyRetry = true

function bareVersion(version: string): string {
  return version.replace(/^v/, '')
}

function valid(value: ApplicationUpdate): boolean {
  if (!value || typeof value !== 'object') return false
  if (value.update_available === false)
    return value.latest_version === null && value.release_url === null
  if (value.update_available !== true || typeof value.latest_version !== 'string') return false
  const version = bareVersion(value.latest_version)
  if (value.latest_version.length > 200 || !stable.test(version)) return false
  const base = 'https://github.com/xbit18/violarr/releases/tag/'
  return value.release_url === `${base}${version}` || value.release_url === `${base}v${version}`
}

async function refresh() {
  if (disposed || controller) return
  controller = new AbortController()
  timeout = setTimeout(() => controller?.abort(), 10_000)
  try {
    const result = await api.applicationUpdate(controller.signal)
    if (!disposed && !controller.signal.aborted && valid(result))
      update.value = result.update_available
        ? { ...result, latest_version: bareVersion(result.latest_version!) }
        : null
  } catch {
    // Optional notification: remain quiet and retain only a validated result.
  } finally {
    clearTimeout(timeout)
    controller = undefined
    if (!disposed) {
      const delay = earlyRetry && !update.value ? 15_000 : 3_600_000
      earlyRetry = false
      timer = setTimeout(refresh, delay)
    }
  }
}
onMounted(refresh)
onBeforeUnmount(() => {
  disposed = true
  clearTimeout(timer)
  clearTimeout(timeout)
  controller?.abort()
})
</script>

<template>
  <a
    v-if="update"
    class="application-update"
    :href="update.release_url!"
    :title="locale.t('applicationUpdate.available', { version: update.latest_version! })"
    target="_blank"
    rel="noopener noreferrer"
  >
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      aria-hidden="true"
      focusable="false"
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M12 16V8m-4 4 4-4 4 4" />
    </svg>
    <span>{{ locale.t('applicationUpdate.available', { version: update.latest_version! }) }}</span>
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M14 3h7v7m0-7L10 14m0-9H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5" />
    </svg>
  </a>
</template>
