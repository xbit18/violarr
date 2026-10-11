<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api, ApiError, apiErrorMessage } from '../api/client'
import { intervalSeconds, units, type IntervalUnit } from './updateInterval'
import ToggleSwitch from '../components/ToggleSwitch.vue'
import { useAppStore } from '../composables/appStore'
import type { PublicSettings } from '../api/types'
import { useLocale } from '../i18n'
const props = defineProps<{
  settings?: PublicSettings
  saveSettings?: (settings: PublicSettings) => Promise<unknown>
}>()
const store = useAppStore()
const { t } = useLocale()
const enabled = ref(true)
const interval = ref(86400)
const unit = ref<IntervalUnit>('days')
const intervalUnits: IntervalUnit[] = ['minutes', 'hours', 'days']
const amount = ref('1')
let initialized = false
let intervalEdited = false
let enabledEdited = false
let originalDisplay = '1'
function editInterval(event: Event) {
  amount.value = (event.target as HTMLInputElement).value
  intervalEdited = true
}
function editEnabled() {
  enabledEdited = true
}
function display(seconds: number) {
  amount.value = String(seconds / units[unit.value])
  originalDisplay = amount.value
}
function seconds() {
  return amount.value === originalDisplay
    ? interval.value
    : intervalSeconds(amount.value, unit.value)
}
function changeUnit(event: Event) {
  const canonical = seconds()
  if (canonical === null) {
    localError.value = t('database.intervalError')
    ;(event.target as HTMLSelectElement).value = unit.value
    return
  }
  interval.value = canonical
  intervalEdited = true
  unit.value = (event.target as HTMLSelectElement).value as IntervalUnit
  display(canonical)
}
const localError = ref('')
const saving = ref(false)
const pending = ref(false)
let accepted = false
const feedback = ref('')
const pollingError = ref('')
const progress = computed(() => store.state.status?.updater)
const active = computed(() => pending.value || !!progress.value?.updating)
const phase = computed(
  () => progress.value?.phase ?? (progress.value?.updating ? 'checking' : 'idle'),
)
let mounted = true
let timer: ReturnType<typeof setTimeout> | undefined
let controller: AbortController | undefined
let triggerController: AbortController | undefined
async function poll() {
  const observeAcceptance = accepted
  controller = new AbortController()
  try {
    const status = await api.status(controller.signal)
    if (mounted) {
      store.state.status = status
      pollingError.value = ''
      if (observeAcceptance) {
        pending.value = false
        accepted = false
        feedback.value = ''
      }
      // Recover initialization only; normal polling must not reload settings or Prowlarr.
      if (!source.value) {
        const settings = await api.settings(controller.signal)
        if (mounted && !source.value) {
          store.state.settings = settings
          store.state.error = null
        }
      }
    }
  } catch (error) {
    if (mounted && !(error instanceof ApiError && error.status === 503)) {
      pollingError.value = apiErrorMessage(error, t('error.unexpected'))
    }
  } finally {
    if (mounted) timer = setTimeout(poll, 2000)
  }
}
async function checkNow() {
  if (active.value) return
  pending.value = true
  feedback.value = ''
  triggerController = new AbortController()
  try {
    await api.triggerDatabaseUpdate(triggerController.signal)
    if (mounted) {
      accepted = true
      feedback.value = t('database.accepted')
    }
  } catch (error) {
    if (mounted)
      feedback.value =
        error instanceof ApiError && error.status === 409
          ? t('database.busy')
          : apiErrorMessage(error, t('error.unexpected'))
  } finally {
    if (mounted && !accepted) pending.value = false
  }
}
onMounted(async () => {
  if (!props.settings) await store.loadAll()
  if (mounted) void poll()
})
onUnmounted(() => {
  mounted = false
  clearTimeout(timer)
  controller?.abort()
  triggerController?.abort()
})
const source = computed(() => props.settings ?? store.state.settings)
watch(
  source,
  (settings) => {
    if (settings) {
      if (initialized || !enabledEdited) enabled.value = settings.database_update.enabled
      if (!initialized && intervalEdited) {
        initialized = true
        return
      }
      initialized = true
      interval.value = settings.database_update.interval_seconds
      unit.value =
        interval.value % 86400 === 0 ? 'days' : interval.value % 3600 === 0 ? 'hours' : 'minutes'
      display(interval.value)
    }
  },
  { immediate: true },
)
async function save() {
  localError.value = ''
  const canonical = seconds()
  if (canonical === null) {
    localError.value = t('database.intervalError')
    return
  }
  if (!source.value || saving.value) return
  saving.value = true
  try {
    await (props.saveSettings ?? store.saveSettings)({
      ...source.value,
      database_update: { enabled: enabled.value, interval_seconds: canonical },
    })
  } catch {
    /* Store or parent operation exposes the actionable error. */
  } finally {
    saving.value = false
  }
}
</script>
<template>
  <section class="page">
    <div class="page-heading">
      <div>
        <p class="eyebrow">{{ t('common.settings') }}</p>
        <h1>{{ t('database.title') }}</h1>
        <p>{{ t('database.intro') }}</p>
      </div>
    </div>
    <article class="card form-card">
      <button class="button" type="button" :disabled="active" @click="checkNow">
        {{ t('database.checkNow') }}
      </button>
      <p role="status" aria-live="polite">
        {{
          pending
            ? feedback || t('database.checking')
            : phase !== 'idle'
              ? t(`database.${phase}`)
              : feedback
        }}
      </p>
      <p v-if="feedback && !pending && phase !== 'idle'" role="status">{{ feedback }}</p>
      <p v-if="pollingError" class="inline-error" role="alert">{{ pollingError }}</p>
      <ToggleSwitch
        v-model="enabled"
        @update:model-value="editEnabled"
        :label="t('database.automatic')"
        :description="t('database.automaticDescription')"
      />
      <div class="interval-controls">
        <label
          >{{ t('database.interval')
          }}<input
            :value="amount"
            @input="editInterval"
            type="number"
            step="any"
            inputmode="decimal" /></label
        ><label
          >{{ t('database.unit')
          }}<select :value="unit" @change="changeUnit">
            <option v-for="name in intervalUnits" :key="name" :value="name">
              {{ t(`database.${name}`) }}
            </option>
          </select></label
        >
      </div>
      <p class="field-help">{{ t('database.range') }}</p>
      <p v-if="localError || store.state.error" class="inline-error" role="alert">
        {{ localError || store.state.error }}
      </p>
      <p v-if="store.state.feedback" class="success-message" role="status">
        {{ store.state.feedback }}
      </p>
      <button class="button" type="button" :disabled="saving || store.state.saving" @click="save">
        {{ saving || store.state.saving ? t('common.saving') : t('database.save') }}
      </button>
    </article>
  </section>
</template>
<style scoped>
.interval-controls {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
}
.interval-controls label {
  flex: 1 1 10rem;
  min-width: 0;
}
</style>
