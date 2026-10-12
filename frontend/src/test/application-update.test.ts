import { render, screen } from '@testing-library/vue'
import { flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ApplicationUpdateNotification from '../components/ApplicationUpdateNotification.vue'
import { api } from '../api/client'
import { useLocale } from '../i18n'

const hidden = { update_available: false, latest_version: null, release_url: null }
const available = {
  update_available: true,
  latest_version: '1.10.0',
  release_url: 'https://github.com/xbit18/violarr/releases/tag/v1.10.0',
}

beforeEach(() => {
  vi.useFakeTimers()
  useLocale().setLocale('it')
  vi.spyOn(api, 'applicationUpdate').mockResolvedValue(hidden)
})
afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it('hides initially and retries only once early before hourly polling', async () => {
  vi.mocked(api.applicationUpdate).mockResolvedValueOnce(hidden).mockResolvedValue(available)
  const view = render(ApplicationUpdateNotification)
  expect(screen.queryByRole('link')).toBeNull()
  await flushPromises()
  await vi.advanceTimersByTimeAsync(15_000)
  expect(screen.getByRole('link', { name: 'v1.10.0 disponibile' })).toBeTruthy()
  expect(api.applicationUpdate).toHaveBeenCalledTimes(2)
  await vi.advanceTimersByTimeAsync(3_600_000)
  expect(api.applicationUpdate).toHaveBeenCalledTimes(3)
  view.unmount()
  await vi.advanceTimersByTimeAsync(3_600_000)
  expect(api.applicationUpdate).toHaveBeenCalledTimes(3)
})

it.each(['1.10.0', 'v1.10.0'])(
  'renders an exact safe anchor with one v prefix for %s and reactive locales',
  async (latest_version) => {
    vi.mocked(api.applicationUpdate).mockResolvedValue({ ...available, latest_version })
    const { container } = render(ApplicationUpdateNotification)
    await flushPromises()
    const anchor = screen.getByRole('link', { name: 'v1.10.0 disponibile' })
    expect(anchor.getAttribute('title')).toBe('v1.10.0 disponibile')
    expect(anchor.getAttribute('href')).toBe(available.release_url)
    expect(anchor.getAttribute('target')).toBe('_blank')
    expect(anchor.getAttribute('rel')).toBe('noopener noreferrer')
    expect([...anchor.children].map((child) => child.tagName.toLowerCase())).toEqual([
      'svg',
      'span',
      'svg',
    ])
    for (const icon of anchor.querySelectorAll('svg')) {
      expect(icon.getAttribute('aria-hidden')).toBe('true')
      expect(icon.getAttribute('focusable')).toBe('false')
    }
    expect(container.querySelector('button')).toBeNull()
    useLocale().setLocale('en')
    await flushPromises()
    expect(screen.getByRole('link', { name: 'v1.10.0 available' })).toBe(anchor)
    expect(anchor.getAttribute('title')).toBe('v1.10.0 available')
  },
)

it.each(['1.10.0', 'v1.10.0', '1.10.0+build.7', 'v1.10.0+build.7'])(
  'preserves the exact safe bare release URL for %s',
  async (latest_version) => {
    const version = latest_version.replace(/^v/, '')
    const release_url = `https://github.com/xbit18/violarr/releases/tag/${version}`
    vi.mocked(api.applicationUpdate).mockResolvedValue({
      ...available,
      latest_version,
      release_url,
    })
    render(ApplicationUpdateNotification)
    await flushPromises()
    const anchor = screen.getByRole('link', { name: `v${version} disponibile` })
    expect(anchor.getAttribute('href')).toBe(release_url)
    expect(anchor.getAttribute('title')).toBe(`v${version} disponibile`)
    useLocale().setLocale('en')
    await flushPromises()
    expect(screen.getByRole('link', { name: `v${version} available` })).toBe(anchor)
    expect(anchor.getAttribute('title')).toBe(`v${version} available`)
  },
)

it.each([
  hidden,
  { ...available, update_available: 'true' },
  { ...available, latest_version: '1.10.0-rc.1' },
  { ...available, latest_version: 'vv1.10.0' },
  { ...available, latest_version: 'V1.10.0' },
  { ...available, latest_version: ' v1.10.0' },
  { ...available, latest_version: 'v01.10.0' },
  { ...available, latest_version: 'v1.10.0-rc.1' },
  { ...available, latest_version: 'v1.10.0', release_url: `${available.release_url}?x=1` },
  {
    ...available,
    latest_version: 'v1.10.0',
    release_url: available.release_url.replace('/v', '/vv'),
  },
  { ...available, latest_version: '1.10.0\n', release_url: `${available.release_url}\n` },
  { ...available, release_url: 'https://evil.test/' },
  { ...available, release_url: `${available.release_url}?x=1` },
  { ...available, release_url: available.release_url.replace('1.10.0', '1.11.0') },
])('hides unavailable or malformed data %j', async (value) => {
  vi.mocked(api.applicationUpdate).mockResolvedValue(value as never)
  render(ApplicationUpdateNotification)
  await flushPromises()
  expect(screen.queryByRole('link')).toBeNull()
})

it('quietly preserves last valid result on refresh failure, then clears a valid hidden result', async () => {
  vi.mocked(api.applicationUpdate)
    .mockResolvedValueOnce(available)
    .mockRejectedValueOnce(new Error('offline'))
    .mockResolvedValue(hidden)
  render(ApplicationUpdateNotification)
  await flushPromises()
  await vi.advanceTimersByTimeAsync(3_600_000)
  expect(screen.getByRole('link')).toBeTruthy()
  await vi.advanceTimersByTimeAsync(3_600_000)
  expect(screen.queryByRole('link')).toBeNull()
})

it('initial failures are silent and get only one early retry', async () => {
  vi.mocked(api.applicationUpdate).mockRejectedValue(new Error('offline'))
  render(ApplicationUpdateNotification)
  await flushPromises()
  await vi.advanceTimersByTimeAsync(30_000)
  expect(api.applicationUpdate).toHaveBeenCalledTimes(2)
  expect(screen.queryByRole('link')).toBeNull()
})

it('does not overlap requests and aborts pending work on unmount', async () => {
  let signal: AbortSignal | undefined
  vi.mocked(api.applicationUpdate).mockImplementation((value) => {
    signal = value
    return new Promise(() => {})
  })
  const view = render(ApplicationUpdateNotification)
  await vi.advanceTimersByTimeAsync(7_200_000)
  expect(api.applicationUpdate).toHaveBeenCalledTimes(1)
  view.unmount()
  expect(signal?.aborted).toBe(true)
})

it('aborts a request after its bounded timeout without creating an overlap', async () => {
  let signal: AbortSignal | undefined
  vi.mocked(api.applicationUpdate).mockImplementation((value) => {
    signal = value
    return new Promise(() => {})
  })
  render(ApplicationUpdateNotification)
  await vi.advanceTimersByTimeAsync(10_000)
  expect(signal?.aborted).toBe(true)
  expect(api.applicationUpdate).toHaveBeenCalledTimes(1)
})

it.each(['timeout', 'unmount'])(
  'cancels a pending real-client body on %s',
  async (cancellation) => {
    vi.mocked(api.applicationUpdate).mockRestore()
    let transport: AbortSignal | undefined
    const fetchMock = vi.fn((_path, init?: RequestInit) => {
      transport = init?.signal as AbortSignal
      const body = new ReadableStream({
        start(stream) {
          transport!.addEventListener('abort', () => stream.error(transport!.reason), {
            once: true,
          })
        },
      })
      return Promise.resolve(new Response(body))
    })
    vi.stubGlobal('fetch', fetchMock)
    const view = render(ApplicationUpdateNotification)
    await flushPromises()
    if (cancellation === 'timeout') await vi.advanceTimersByTimeAsync(10_000)
    else view.unmount()
    await flushPromises()
    expect(transport?.aborted).toBe(true)
    expect(screen.queryByRole('link')).toBeNull()
    if (cancellation === 'timeout') {
      fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(available)))
      await vi.advanceTimersByTimeAsync(15_000)
      expect(fetchMock).toHaveBeenCalledTimes(2)
      expect(screen.getByRole('link')).toBeTruthy()
      view.unmount()
    }
    await vi.advanceTimersByTimeAsync(3_600_000)
    expect(fetchMock).toHaveBeenCalledTimes(cancellation === 'timeout' ? 2 : 1)
    expect(vi.getTimerCount()).toBe(0)
  },
)
