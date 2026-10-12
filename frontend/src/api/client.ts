import type {
  AppStatus,
  ApplicationUpdate,
  IndexerResult,
  ProwlarrStatus,
  PublicSettings,
  ResultProcessing,
} from './types'
import { useLocale } from '../i18n'

const { t } = useLocale()

export interface ApiErrorDetail {
  code: string
  message: string
  hint?: string
  stage?: string
  upstream_status?: number
  upstream_message?: string
}

function structuredDetail(value: unknown): ApiErrorDetail | null {
  if (!value || typeof value !== 'object') return null

  const candidate = value as Record<string, unknown>
  if (typeof candidate.code !== 'string' || typeof candidate.message !== 'string') return null

  return {
    code: candidate.code,
    message: candidate.message,
    ...(typeof candidate.hint === 'string' ? { hint: candidate.hint } : {}),
    ...(typeof candidate.stage === 'string' ? { stage: candidate.stage } : {}),
    ...(typeof candidate.upstream_status === 'number'
      ? { upstream_status: candidate.upstream_status }
      : {}),
    ...(typeof candidate.upstream_message === 'string'
      ? { upstream_message: candidate.upstream_message }
      : {}),
  }
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number | null = null,
    readonly source: 'client' | 'server' = 'client',
    readonly detail: ApiErrorDetail | null = null,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export const REQUEST_TIMEOUT_MS = 10_000

export async function requestJson<T>(
  path: string,
  init?: RequestInit,
  timeoutMs = REQUEST_TIMEOUT_MS,
): Promise<T> {
  const controller = new AbortController()
  let timedOut = false
  const forwardAbort = () => controller.abort(init?.signal?.reason)
  if (init?.signal?.aborted) forwardAbort()
  else init?.signal?.addEventListener('abort', forwardAbort, { once: true })
  const timeout = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)
  try {
    let response: Response
    try {
      response = await fetch(path, {
        ...init,
        headers: { 'Content-Type': 'application/json', ...init?.headers },
        signal: controller.signal,
      })
    } catch (error) {
      if (
        controller.signal.aborted ||
        (error instanceof DOMException && error.name === 'AbortError')
      )
        throw error
      throw new ApiError(t('error.unreachable'))
    }
    if (!response.ok) {
      let detail: unknown
      try {
        const body = (await response.json()) as { detail?: unknown }
        detail = body.detail
      } catch (error) {
        if (controller.signal.aborted) throw error
        detail = null
      }

      const objectDetail = structuredDetail(detail)
      const serverMessage =
        typeof detail === 'string' && detail.trim()
          ? detail
          : objectDetail?.message?.trim()
            ? objectDetail.message
            : null
      const message = serverMessage ?? t('error.requestFailed', { status: response.status })

      throw new ApiError(
        message,
        response.status,
        serverMessage ? 'server' : 'client',
        objectDetail,
      )
    }
    return (await response.json()) as T
  } catch (error) {
    if (timedOut) throw new ApiError(t('error.timeout'))
    if (init?.signal?.aborted || (error instanceof DOMException && error.name === 'AbortError')) {
      throw new ApiError(t('error.canceled'))
    }
    throw error
  } finally {
    clearTimeout(timeout)
    init?.signal?.removeEventListener('abort', forwardAbort)
  }
}

export function apiErrorMessage(error: unknown, fallback: string) {
  if (!(error instanceof ApiError)) return fallback
  return error.source === 'server'
    ? useLocale().localizeServerMessage(error.message, fallback)
    : error.message
}

const json = (body: unknown): RequestInit => ({ method: 'PUT', body: JSON.stringify(body) })

export const api = {
  applicationUpdate: (signal?: AbortSignal) =>
    requestJson<ApplicationUpdate>('/webapi/application-update', { signal }),
  status: (signal?: AbortSignal) => requestJson<AppStatus>('/webapi/status', { signal }),
  triggerDatabaseUpdate: (signal?: AbortSignal) =>
    requestJson<{ accepted: true }>('/webapi/database-update', { method: 'POST', signal }),
  settings: (signal?: AbortSignal) => requestJson<PublicSettings>('/webapi/settings', { signal }),
  saveSettings: (settings: PublicSettings) =>
    requestJson<PublicSettings>('/webapi/settings', json(settings)),
  resultProcessing: () => requestJson<ResultProcessing>('/webapi/result-processing'),
  saveResultProcessing: (value: ResultProcessing) =>
    requestJson<ResultProcessing>('/webapi/result-processing', json(value)),
  prowlarrStatus: () => requestJson<ProwlarrStatus>('/webapi/prowlarr/status'),
  testProwlarr: () =>
    requestJson<{ connected: true; error: null }>(
      '/webapi/prowlarr/test',
      { method: 'POST' },
      20_000,
    ),

  installIndexer: () =>
    requestJson<IndexerResult>('/webapi/prowlarr/indexer', { method: 'POST' }, 100_000),
}
