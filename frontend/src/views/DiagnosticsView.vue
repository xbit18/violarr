<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import { ApiError, apiErrorMessage } from '../api/client'
import {
  diagnosticsApi,
  exportReport,
  exportHistory,
  observedStages,
  type SearchInput,
  type SearchType,
  type SearchReport,
  type MonitoredRequest,
  type MonitoringStatus,
} from '../api/diagnostics'
import ToggleSwitch from '../components/ToggleSwitch.vue'
import { useLocale } from '../i18n'

const { t } = useLocale()
const tab = ref<'search' | 'requests'>('search')
const advanced = ref(false)
const form = reactive({
  t: 'search' as SearchType,
  q: '',
  imdbid: '',
  tmdbid: '',
  season: '',
  ep: '',
  cat: '',
  limit: '100',
  offset: '0',
})
const report = ref<SearchReport | null>(null)
const busy = ref(false)
const error = ref('')
const replayNotice = ref(false)
const preview = ref('')
const historyPreview = ref('')
const monitoring = ref<MonitoringStatus | null>(null)
const history = ref<MonitoredRequest[]>([])
const inspected = ref<MonitoredRequest | null>(null)
const historyBusy = ref(false)
const actionBusy = ref(false)
const historyError = ref('')
const stages = computed(() => (report.value ? observedStages(report.value) : []))
const counts = [
  'candidates',
  'excluded',
  'retained',
  'outside_page',
  'selected',
  'returned',
] as const
let disposed = false
let generation = 0
let searchController: AbortController | null = null
let historyController: AbortController | null = null
let actionController: AbortController | null = null
let timer: ReturnType<typeof setTimeout> | undefined
const succeeded = computed(
  () =>
    report.value &&
    report.value.errors.length === 0 &&
    stages.value.every((name) => report.value!.stages[name]?.status === 'success') &&
    (report.value.report_version === 1 ||
      report.value.strategies.every((strategy) => strategy.status === 'success')),
)
function message(cause: unknown) {
  if (cause instanceof ApiError && cause.status === 503) return t('diagnostics.maintenance')
  return apiErrorMessage(cause, t('diagnostics.requestError'))
}
function integer(value: string | number, min: number, max: number, nullable = false) {
  if (nullable && value === '') return null
  const number = String(value).trim() === '' ? NaN : Number(value)
  if (!Number.isInteger(number) || number < min || number > max)
    throw new Error('Invalid parameters')
  return number
}
function parameters(): SearchInput {
  if ([form.q, form.imdbid, form.cat].some((value) => value.length > 512))
    throw new Error('Invalid parameters')
  return {
    t: form.t,
    q: form.q || null,
    imdbid: form.t === 'search' ? null : form.imdbid || null,
    tmdbid: form.t === 'movie' ? integer(form.tmdbid, -1e9, 1e9, true) : null,
    season: form.t === 'tvsearch' ? integer(form.season, -1e9, 1e9, true) : null,
    ep: form.t === 'tvsearch' ? integer(form.ep, -1e9, 1e9, true) : null,
    cat: form.cat || null,
    limit: integer(form.limit, 1, 200)!,
    offset: integer(form.offset, 0, 1e6)!,
  }
}
async function execute(input: SearchInput) {
  if (busy.value || disposed) return
  busy.value = true
  error.value = ''
  report.value = null
  preview.value = ''
  const controller = new AbortController()
  searchController = controller
  try {
    const result = await diagnosticsApi.search(input, controller.signal)
    if (
      !result ||
      (result.report_version !== 1 && result.report_version !== 2) ||
      !result.stages ||
      !result.counts ||
      !Array.isArray(result.releases) ||
      !Array.isArray(result.errors) ||
      (result.report_version === 2 &&
        (!Array.isArray(result.strategies) || !Array.isArray(result.provenance)))
    )
      throw new ApiError(t('diagnostics.missingReport'))
    if (!disposed && searchController === controller) report.value = result
  } catch (cause) {
    if (!disposed && searchController === controller) error.value = message(cause)
  } finally {
    if (!disposed && searchController === controller) {
      busy.value = false
      searchController = null
    }
  }
}
function submit() {
  replayNotice.value = false
  try {
    void execute(parameters())
  } catch {
    error.value = t('diagnostics.invalid')
  }
}
function stopPolling() {
  clearTimeout(timer)
  timer = undefined
  generation++
  historyController?.abort()
  historyController = null
  historyBusy.value = false
}
function schedule() {
  clearTimeout(timer)
  if (!disposed && tab.value === 'requests') timer = setTimeout(() => void refresh(), 5000)
}
async function refresh() {
  if (disposed || tab.value !== 'requests' || historyBusy.value || actionBusy.value) return
  clearTimeout(timer)
  historyBusy.value = true
  const current = ++generation
  const controller = new AbortController()
  historyController = controller
  try {
    const [status, entries] = await Promise.all([
      diagnosticsApi.monitoring(controller.signal),
      diagnosticsApi.requests(controller.signal),
    ])
    if (disposed || current !== generation) return
    monitoring.value = status
    history.value = entries.requests
    historyError.value = ''
    if (inspected.value && !entries.requests.some((entry) => entry.id === inspected.value!.id))
      inspected.value = null
  } catch (cause) {
    controller.abort()
    if (!disposed && current === generation) historyError.value = message(cause)
  } finally {
    if (!disposed && current === generation) {
      historyBusy.value = false
      historyController = null
      schedule()
    }
  }
}
function selectTab(value: 'search' | 'requests') {
  if (tab.value === value) return
  stopPolling()
  historyPreview.value = ''
  tab.value = value
  if (value === 'requests') void refresh()
}
async function changeMonitoring(value: boolean) {
  if (actionBusy.value) return
  stopPolling()
  actionBusy.value = true
  historyError.value = ''
  const controller = new AbortController()
  actionController = controller
  try {
    const status = await diagnosticsApi.configure(value, controller.signal)
    if (!disposed) monitoring.value = status
  } catch (cause) {
    if (!disposed) historyError.value = message(cause)
  } finally {
    if (!disposed) {
      actionBusy.value = false
      actionController = null
      schedule()
    }
  }
}
async function clearHistory() {
  if (actionBusy.value) return
  stopPolling()
  historyPreview.value = ''
  actionBusy.value = true
  historyError.value = ''
  const controller = new AbortController()
  actionController = controller
  try {
    const result = await diagnosticsApi.clear(controller.signal)
    if (!disposed) {
      history.value = result.requests
      inspected.value = null
      if (monitoring.value) monitoring.value.count = result.requests.length
    }
  } catch (cause) {
    if (!disposed) historyError.value = message(cause)
  } finally {
    if (!disposed) {
      actionBusy.value = false
      actionController = null
      schedule()
    }
  }
}
function replay() {
  const entry = inspected.value
  if (!entry?.replayable || busy.value) return
  // Preserve original ignored parameters too: replay must not normalize or silently rewrite history.
  const input = { ...entry.original } as unknown as SearchInput
  form.t = input.t
  for (const key of ['q', 'imdbid', 'tmdbid', 'season', 'ep', 'cat', 'limit', 'offset'] as const)
    form[key] = String(input[key] ?? (key === 'limit' ? 100 : key === 'offset' ? 0 : ''))
  advanced.value = true
  selectTab('search')
  replayNotice.value = true
  void execute(input)
}
function prepareExport() {
  if (!report.value) return
  try {
    preview.value = exportReport(report.value)
  } catch {
    error.value = t('diagnostics.exportError')
  }
}
async function prepareHistoryExport() {
  if (disposed || actionBusy.value || historyBusy.value || !history.value.length) return
  stopPolling()
  const current = generation
  actionBusy.value = true
  historyError.value = ''
  historyPreview.value = ''
  const controller = new AbortController()
  actionController = controller
  try {
    const result = await diagnosticsApi.requests(controller.signal)
    if (!disposed && current === generation && tab.value === 'requests')
      historyPreview.value = exportHistory(result.requests)
  } catch (cause) {
    if (!disposed && current === generation) historyError.value = message(cause)
  } finally {
    if (!disposed) {
      actionBusy.value = false
      actionController = null
      schedule()
    }
  }
}
function download(content = preview.value, historyExport = false) {
  if (!content) return
  let url: string | null = null
  try {
    url = URL.createObjectURL(new Blob([content], { type: 'application/json' }))
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = historyExport
      ? 'violarr-request-history-v1.json'
      : `violarr-diagnostics-v${report.value?.report_version ?? 2}.json`
    anchor.click()
  } catch {
    if (historyExport) historyError.value = t('diagnostics.exportError')
    else error.value = t('diagnostics.exportError')
  } finally {
    if (url) URL.revokeObjectURL(url)
  }
}
onBeforeUnmount(() => {
  disposed = true
  stopPolling()
  searchController?.abort()
  actionController?.abort()
})
</script>

<template>
  <section class="page diagnostics">
    <div class="page-heading">
      <div>
        <h1>{{ t('common.diagnostics') }}</h1>
        <p>{{ t('diagnostics.intro') }}</p>
      </div>
    </div>
    <nav class="button-row" :aria-label="t('common.diagnostics')">
      <button
        class="button button--secondary"
        :aria-pressed="tab === 'search'"
        @click="selectTab('search')"
      >
        {{ t('diagnostics.search') }}
      </button>
      <button
        class="button button--secondary"
        :aria-pressed="tab === 'requests'"
        @click="selectTab('requests')"
      >
        {{ t('diagnostics.requests') }}
      </button>
    </nav>
    <template v-if="tab === 'search'">
      <p v-if="replayNotice" class="notice">{{ t('diagnostics.replayNotice') }}</p>
      <form class="card form-card" novalidate :aria-busy="busy" @submit.prevent="submit">
        <fieldset :disabled="busy" class="diagnostics-fields">
          <label
            >{{ t('diagnostics.type')
            }}<select v-model="form.t">
              <option value="search">{{ t('diagnostics.generic') }}</option>
              <option value="movie">{{ t('diagnostics.movie') }}</option>
              <option value="tvsearch">{{ t('diagnostics.tv') }}</option>
            </select></label
          >
          <label
            >{{ t('diagnostics.query') }}<input v-model="form.q" maxlength="512" type="text"
          /></label>
          <button
            type="button"
            class="text-button"
            :aria-expanded="advanced"
            aria-controls="diagnostics-advanced"
            @click="advanced = !advanced"
          >
            {{ t('diagnostics.advanced') }}
          </button>
          <div v-if="advanced" id="diagnostics-advanced" class="diagnostics-grid">
            <label v-if="form.t !== 'search'"
              >IMDb ID<input v-model="form.imdbid" maxlength="512" type="text"
            /></label>
            <label v-if="form.t === 'movie'"
              >TMDb ID<input
                v-model="form.tmdbid"
                type="number"
                min="-1000000000"
                max="1000000000"
                step="1"
            /></label>
            <template v-if="form.t === 'tvsearch'">
              <label
                >{{ t('diagnostics.season')
                }}<input
                  v-model="form.season"
                  type="number"
                  min="-1000000000"
                  max="1000000000"
                  step="1"
              /></label>
              <label
                >{{ t('diagnostics.episode')
                }}<input
                  v-model="form.ep"
                  type="number"
                  min="-1000000000"
                  max="1000000000"
                  step="1"
              /></label>
            </template>
            <label
              >{{ t('diagnostics.categories')
              }}<input v-model="form.cat" maxlength="512" type="text"
            /></label>
            <label
              >{{ t('diagnostics.limit')
              }}<input v-model="form.limit" type="number" min="1" max="200" step="1" required
            /></label>
            <label
              >{{ t('diagnostics.offset')
              }}<input v-model="form.offset" type="number" min="0" max="1000000" step="1" required
            /></label>
            <p class="diagnostics-wide field-help">{{ t('diagnostics.ignored') }}</p>
          </div>
        </fieldset>
        <div class="button-row">
          <button class="button" :disabled="busy" type="submit">{{ t('diagnostics.run') }}</button
          ><button
            class="button button--secondary"
            :disabled="!report || busy"
            type="button"
            @click="prepareExport"
          >
            {{ t('diagnostics.export') }}
          </button>
        </div>
      </form>
      <p v-if="busy" role="status">{{ t('diagnostics.loading') }}</p>
      <p v-if="error" role="alert" class="inline-error">{{ error }}</p>
      <p v-if="!report && !busy" class="empty-state">{{ t('diagnostics.noReport') }}</p>
      <template v-if="report">
        <article class="card" aria-live="polite">
          <h2>{{ t('diagnostics.summary') }}</h2>
          <p v-if="!succeeded" class="danger-text">{{ t('diagnostics.failed') }}</p>
          <p v-else-if="report.counts.candidates === 0">{{ t('diagnostics.empty') }}</p>
          <p v-else-if="report.counts.returned === 0">{{ t('diagnostics.noReturned') }}</p>
          <p v-else>{{ t('diagnostics.generated', { count: report.counts.returned }) }}</p>
          <p v-if="report.counts.excluded">{{ t('diagnostics.filtered') }}</p>
          <p class="notice">{{ t('diagnostics.boundaries') }}</p>
          <p v-if="report.truncated" class="notice">{{ t('diagnostics.truncated') }}</p>
          <dl class="detail-list">
            <div>
              <dt>{{ t('diagnostics.strategy') }}</dt>
              <dd>{{ report.strategy ?? t('common.notAvailable') }}</dd>
            </div>
            <div>
              <dt>{{ t('diagnostics.duration') }}</dt>
              <dd>{{ report.duration_ms.toFixed(1) }} ms</dd>
            </div>
            <div>
              <dt>{{ t('diagnostics.version') }}</dt>
              <dd>{{ report.application_version }}</dd>
            </div>
            <div>
              <dt>{{ t('diagnostics.snapshot') }}</dt>
              <dd>{{ report.snapshot_version ?? t('common.notAvailable') }}</dd>
            </div>
            <div v-for="name in counts" :key="name">
              <dt>{{ t(`diagnostics.count.${name}`) }}</dt>
              <dd>{{ report.counts[name] }}</dd>
            </div>
          </dl>
          <ul v-if="report.errors.length" class="danger-text">
            <li v-for="issue in report.errors" :key="issue.code">
              {{ t(`diagnostics.stage.${issue.stage}`) }}: {{ issue.message }} ({{ issue.code }})
            </li>
          </ul>
        </article>
        <div class="diagnostics-grid">
          <article v-for="name in stages" :key="name" class="card">
            <h2>{{ t(`diagnostics.stage.${name}`) }}</h2>
            <span
              class="badge"
              :class="{
                'badge--success': report.stages[name]?.status === 'success',
                'badge--danger': report.stages[name]?.status === 'failed',
              }"
              >{{ t(`diagnostics.status.${report.stages[name]?.status ?? 'not_run'}`) }}</span
            >
            <p>{{ report.stages[name]?.duration_ms.toFixed(1) }} ms</p>
          </article>
        </div>
        <article v-if="report.report_version === 2" class="card">
          <h2>{{ t('diagnostics.strategies') }}</h2>
          <div v-for="(strategy, index) in report.strategies" :key="index">
            <h3>{{ strategy.identifier }}</h3>
            <p>
              <span class="badge">{{ t(`diagnostics.status.${strategy.status}`) }}</span> ·
              {{ strategy.duration_ms.toFixed(1) }} ms
            </p>
            <p>
              {{ t('diagnostics.count.candidates') }}:
              {{ strategy.candidates ?? t('common.notAvailable') }} ·
              {{ t('diagnostics.uniqueContribution') }}:
              {{ strategy.unique_contribution ?? t('common.notAvailable') }}
            </p>
          </div>
          <h2>{{ t('diagnostics.provenance') }}</h2>
          <div v-for="item in report.provenance" :key="item.identity">
            <p>{{ item.identity }}</p>
            <p>{{ item.strategies.join(', ') }} · {{ item.occurrences.join(', ') }}</p>
            <p v-for="(evidence, index) in item.match_evidence" :key="index">
              {{ evidence.strategy }} · {{ evidence.field ?? t('common.notAvailable') }} ·
              {{ evidence.match_type ?? t('common.notAvailable') }}
            </p>
            <p>
              {{ t('diagnostics.deduplicated') }}:
              {{ item.deduplicated === null ? t('common.notAvailable') : item.deduplicated }} ·
              {{ t('diagnostics.relevance') }}: {{ item.relevance ?? t('common.notAvailable') }} ·
              {{ t('diagnostics.included') }}:
              {{ item.included === null ? t('common.notAvailable') : item.included }}
            </p>
          </div>
        </article>
        <article class="card">
          <h2>{{ t('diagnostics.context') }}</h2>
          <details>
            <summary>{{ t('diagnostics.original') }}</summary>
            <pre>{{ JSON.stringify(report.original, null, 2) }}</pre>
          </details>
          <details>
            <summary>{{ t('diagnostics.normalized') }}</summary>
            <pre>{{ JSON.stringify(report.normalized, null, 2) }}</pre>
          </details>
          <details>
            <summary>{{ t('diagnostics.settings') }}</summary>
            <pre>{{
              JSON.stringify(
                {
                  processing: report.processing,
                  serialization_settings: report.serialization_settings,
                },
                null,
                2,
              )
            }}</pre>
          </details>
          <details>
            <summary>{{ t('diagnostics.windows') }}</summary>
            <pre>{{ JSON.stringify(report.windows, null, 2) }}</pre>
          </details>
        </article>
        <article class="card">
          <h2>{{ t('diagnostics.candidates') }}</h2>
          <p v-if="!report.releases.length">{{ t('diagnostics.noCandidates') }}</p>
          <div v-for="release in report.releases" :key="release.id" class="diagnostics-release">
            <h3>{{ release.title ?? t('common.notAvailable') }}</h3>
            <span class="badge" :class="{ 'badge--danger': release.status === 'excluded' }">{{
              t(`diagnostics.release.${release.status}`)
            }}</span>
            <dl class="detail-list">
              <div>
                <dt>{{ t('diagnostics.metadata') }}</dt>
                <dd>
                  {{ release.type }} / {{ release.category }} /
                  {{ release.language ?? t('common.notAvailable') }}
                </dd>
              </div>
              <div>
                <dt>{{ t('diagnostics.size') }}</dt>
                <dd>{{ release.size ?? t('common.notAvailable') }}</dd>
              </div>
              <div>
                <dt>{{ t('diagnostics.seeders') }}</dt>
                <dd>{{ release.seeders ?? t('common.notAvailable') }}</dd>
              </div>
              <div>
                <dt>{{ t('diagnostics.provider') }}</dt>
                <dd>{{ release.provider ?? t('common.notAvailable') }}</dd>
              </div>
              <div v-if="release.reason">
                <dt>{{ t('diagnostics.reason') }}</dt>
                <dd>{{ release.reason }}</dd>
              </div>
              <div>
                <dt>{{ t('diagnostics.score') }}</dt>
                <dd>{{ release.score }} / {{ release.score_rules.join(', ') || '—' }}</dd>
              </div>
            </dl>
            <p v-if="release.subtitle_corrected_for_processing">{{ t('diagnostics.subtitle') }}</p>
          </div>
        </article>
      </template>
      <section v-if="preview" class="card" role="region" :aria-label="t('diagnostics.preview')">
        <h2>{{ t('diagnostics.preview') }}</h2>
        <p class="notice">{{ t('diagnostics.privacy') }}</p>
        <pre class="diagnostics-preview">{{ preview }}</pre>
        <div class="button-row">
          <button class="button" @click="download()">{{ t('diagnostics.download') }}</button
          ><button class="button button--secondary" @click="preview = ''">
            {{ t('diagnostics.close') }}
          </button>
        </div>
      </section>
    </template>
    <template v-else>
      <article class="card">
        <ToggleSwitch
          :key="`${monitoring?.enabled}-${actionBusy}`"
          :model-value="monitoring?.enabled ?? false"
          :label="t('diagnostics.monitor')"
          :disabled="!monitoring || actionBusy || historyBusy"
          @update:model-value="changeMonitoring"
        />
        <p>
          {{
            !monitoring
              ? t('diagnostics.monitoringUnknown')
              : monitoring.enabled
                ? t('diagnostics.enabled')
                : t('diagnostics.disabled')
          }}
        </p>
        <p v-if="monitoring">
          {{
            t('diagnostics.historyLimit', {
              count: monitoring.count,
              capacity: monitoring.capacity,
            })
          }}
        </p>
        <div class="button-row">
          <button
            class="button button--secondary"
            :disabled="historyBusy || actionBusy"
            @click="refresh"
          >
            {{ t('diagnostics.refresh') }}</button
          ><button
            class="button button--secondary"
            :disabled="actionBusy || !history.length"
            @click="clearHistory"
          >
            {{ t('diagnostics.clear') }}</button
          ><button
            class="button"
            :disabled="historyBusy || actionBusy || !history.length"
            @click="prepareHistoryExport"
          >
            {{ t('diagnostics.exportHistory') }}
          </button>
        </div>
        <p v-if="historyBusy" role="status">{{ t('diagnostics.refreshing') }}</p>
        <p v-if="historyError" class="inline-error" role="alert">{{ historyError }}</p>
      </article>
      <section
        v-if="historyPreview"
        class="card"
        role="region"
        :aria-label="t('diagnostics.historyPreview')"
      >
        <h2>{{ t('diagnostics.historyPreview') }}</h2>
        <p class="notice">{{ t('diagnostics.privacy') }}</p>
        <pre class="diagnostics-preview">{{ historyPreview }}</pre>
        <div class="button-row">
          <button
            class="button"
            :disabled="actionBusy || historyBusy"
            @click="download(historyPreview, true)"
          >
            {{ t('diagnostics.download') }}
          </button>
          <button class="button button--secondary" @click="historyPreview = ''">
            {{ t('diagnostics.close') }}
          </button>
        </div>
      </section>
      <p v-if="!history.length && !historyBusy && !historyError" class="empty-state">
        {{ t('diagnostics.noHistory') }}
      </p>
      <article v-for="entry in history" :key="entry.id" class="card">
        <p
          v-if="
            entry.errors.length ||
            Object.values(entry.stages).some((stage) => stage.status === 'failed') ||
            entry.strategies?.some((strategy) => strategy.status !== 'success')
          "
          class="danger-text"
        >
          {{ t('diagnostics.failed') }}
        </p>
        <div class="card-heading">
          <div>
            <h2>{{ entry.timestamp }}</h2>
            <p>
              {{ entry.original.t }} ·
              {{ entry.original.q || entry.original.imdbid || entry.original.tmdbid || '—' }}
            </p>
            <p>
              {{ t('diagnostics.count.returned') }}: {{ entry.counts.returned }} ·
              {{ entry.duration_ms.toFixed(1) }} ms · HTTP {{ entry.status_code }}
            </p>
          </div>
          <button
            class="button button--secondary"
            :aria-label="t('diagnostics.inspect', { id: entry.id })"
            @click="inspected = inspected?.id === entry.id ? null : entry"
          >
            {{ t('diagnostics.inspect', { id: entry.id }) }}
          </button>
        </div>
        <template v-if="inspected && inspected.id === entry.id">
          <h2>{{ t('diagnostics.requestDetail') }}</h2>
          <h3>{{ t('diagnostics.original') }}</h3>
          <pre>{{ JSON.stringify(inspected.original, null, 2) }}</pre>
          <h3>{{ t('diagnostics.normalized') }}</h3>
          <pre>{{ JSON.stringify(inspected.normalized, null, 2) }}</pre>
          <p>
            {{ t('diagnostics.strategy') }}: {{ inspected.strategy ?? t('common.notAvailable') }}
          </p>
          <div v-for="(strategy, index) in inspected.strategies ?? []" :key="index">
            <p>
              {{ strategy.identifier }} · {{ t(`diagnostics.status.${strategy.status}`) }} ·
              {{ strategy.duration_ms.toFixed(1) }} ms · {{ t('diagnostics.count.candidates') }}:
              {{ strategy.candidates ?? t('common.notAvailable') }} ·
              {{ t('diagnostics.uniqueContribution') }}:
              {{ strategy.unique_contribution ?? t('common.notAvailable') }}
            </p>
          </div>
          <dl class="detail-list">
            <div v-for="name in counts" :key="name">
              <dt>{{ t(`diagnostics.count.${name}`) }}</dt>
              <dd>{{ inspected.counts[name] }}</dd>
            </div>
            <div v-for="(stage, name) in inspected.stages" :key="name">
              <dt>{{ t(`diagnostics.stage.${name}`) }}</dt>
              <dd>
                {{ t(`diagnostics.status.${stage?.status ?? 'not_run'}`) }} ·
                {{ stage?.duration_ms.toFixed(1) }} ms
              </dd>
            </div>
          </dl>
          <p>{{ t('diagnostics.boundaries') }}</p>
          <p v-for="issue in inspected.errors" :key="issue.code" class="danger-text">
            {{ issue.message }} ({{ issue.code }})
          </p>
          <p v-if="!inspected.replayable" class="notice">{{ t('diagnostics.notReplayable') }}</p>
          <button class="button" :disabled="!inspected.replayable || busy" @click="replay">
            {{ t('diagnostics.replay') }}
          </button>
        </template>
      </article>
    </template>
  </section>
</template>

<style scoped>
.diagnostics-fields {
  border: 0;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 16px;
  min-width: 0;
}
.diagnostics-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}
.diagnostics-wide {
  grid-column: 1 / -1;
}
.diagnostics select {
  width: 100%;
  min-height: 39px;
  border: 1px solid var(--border);
  border-radius: 7px;
  padding: 7px 9px;
  color: var(--text);
  background: var(--surface-elevated);
}
.diagnostics pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-width: 100%;
  font-size: 12px;
}
.diagnostics-preview {
  max-height: 360px;
  overflow: auto;
}
.diagnostics summary {
  cursor: pointer;
  padding: 8px 0;
}
.diagnostics-release {
  border-top: 1px solid var(--border);
  padding-top: 10px;
}
.diagnostics h3 {
  overflow-wrap: anywhere;
}
@media (max-width: 600px) {
  .diagnostics-grid {
    grid-template-columns: 1fr;
  }
  .card-heading {
    flex-wrap: wrap;
  }
}
</style>
