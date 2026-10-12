import { flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, REQUEST_TIMEOUT_MS, requestJson } from '../api/client'
import { useLocale } from '../i18n'

afterEach(() => vi.useRealTimers())
beforeEach(() => useLocale().setLocale('en'))

describe('API client', () => {
  it('posts the manual trigger and forwards status cancellation', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ accepted: true }), { status: 202 })),
    )
    expect(await api.triggerDatabaseUpdate()).toEqual({ accepted: true })
    expect(fetch).toHaveBeenCalledWith(
      '/webapi/database-update',
      expect.objectContaining({ method: 'POST' }),
    )
    const caller = new AbortController()
    vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify({})))
    await api.status(caller.signal)
    expect(fetch).toHaveBeenLastCalledWith(
      '/webapi/status',
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
  })
  it('normalizes JSON and non-JSON failures without exposing response internals', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: 'Interval must be at least 60 seconds' }), {
          status: 422,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )

    await expect(requestJson('/webapi/settings')).rejects.toEqual(
      expect.objectContaining({ message: 'Interval must be at least 60 seconds', status: 422 }),
    )

    vi.mocked(fetch).mockResolvedValueOnce(new Response('proxy failure', { status: 502 }))
    await expect(requestJson('/webapi/status')).rejects.toEqual(
      new ApiError('Request failed (502). Please try again.', 502),
    )
  })

  it('aborts requests after the bounded timeout', async () => {
    vi.useFakeTimers()
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_path, init) =>
          new Promise((_resolve, reject) => {
            init?.signal?.addEventListener('abort', () =>
              reject(new DOMException('Aborted', 'AbortError')),
            )
          }),
      ),
    )

    const request = requestJson('/webapi/status')
    const rejection = expect(request).rejects.toEqual(
      new ApiError('The request timed out. Please try again.'),
    )
    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS)

    await rejection
  })

  it('respects caller cancellation and removes the forwarding listener', async () => {
    const caller = new AbortController()
    const remove = vi.spyOn(caller.signal, 'removeEventListener')
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_path, init) =>
          new Promise((_resolve, reject) => {
            init?.signal?.addEventListener('abort', () =>
              reject(new DOMException('Aborted', 'AbortError')),
            )
          }),
      ),
    )

    const request = requestJson('/webapi/status', { signal: caller.signal })
    caller.abort()

    await expect(request).rejects.toEqual(new ApiError('The request was canceled.'))
    expect(remove).toHaveBeenCalledWith('abort', expect.any(Function))
  })

  it.each([
    [200, 'timeout'],
    [502, 'timeout'],
    [200, 'caller'],
    [502, 'caller'],
  ])('aborts and settles a pending %s response body on %s', async (status, cancellation) => {
    vi.useFakeTimers()
    const caller = new AbortController()
    const remove = vi.spyOn(caller.signal, 'removeEventListener')
    let transport: AbortSignal | undefined
    vi.stubGlobal(
      'fetch',
      vi.fn((_path, init) => {
        transport = init?.signal as AbortSignal
        const body = new ReadableStream({
          start(stream) {
            transport!.addEventListener('abort', () => stream.error(transport!.reason), {
              once: true,
            })
          },
        })
        return Promise.resolve(new Response(body, { status }))
      }),
    )
    let outcome: unknown
    const request = requestJson('/webapi/status', { signal: caller.signal }).catch((error) => {
      outcome = error
    })
    await flushPromises()
    expect(remove).not.toHaveBeenCalled()
    if (cancellation === 'timeout') await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS)
    else caller.abort()
    await flushPromises()
    expect(transport?.aborted).toBe(true)
    expect(outcome).toEqual(
      new ApiError(
        cancellation === 'timeout'
          ? 'The request timed out. Please try again.'
          : 'The request was canceled.',
      ),
    )
    await request
    expect(remove).toHaveBeenCalledWith('abort', expect.any(Function))
    expect(vi.getTimerCount()).toBe(0)
  })

  it('preserves successful-response JSON errors and cleans up after body processing', async () => {
    vi.useFakeTimers()
    const caller = new AbortController()
    const remove = vi.spyOn(caller.signal, 'removeEventListener')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('invalid JSON')))
    await expect(requestJson('/webapi/status', { signal: caller.signal })).rejects.toBeInstanceOf(
      SyntaxError,
    )
    expect(remove).toHaveBeenCalledWith('abort', expect.any(Function))
    expect(vi.getTimerCount()).toBe(0)
  })

  it('preserves structured server errors and network/cancellation messages', async () => {
    const detail = { code: 'update_busy', message: 'Already updating', hint: 'Try later' }
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail }), { status: 409 })),
    )
    await expect(requestJson('/webapi/status')).rejects.toEqual(
      new ApiError(detail.message, 409, 'server', detail),
    )
    vi.mocked(fetch).mockRejectedValueOnce(new TypeError('internal network detail'))
    await expect(requestJson('/webapi/status')).rejects.toEqual(
      new ApiError('The service could not be reached. Check that it is running and try again.'),
    )
    vi.mocked(fetch).mockRejectedValueOnce(new DOMException('Aborted', 'AbortError'))
    await expect(requestJson('/webapi/status')).rejects.toEqual(
      new ApiError('The request was canceled.'),
    )
  })
})
