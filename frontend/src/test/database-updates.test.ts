import { fireEvent, render, screen, cleanup } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import DatabaseUpdatesView from '../views/DatabaseUpdatesView.vue'
import { intervalSeconds } from '../views/updateInterval'
import { api, ApiError } from '../api/client'
import { useAppStore } from '../composables/appStore'
import { useLocale } from '../i18n'
import { settingsFixture, statusFixture } from './fixtures'

describe('database updates', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    useLocale().setLocale('en')
    useAppStore().state.status = null
    useAppStore().state.error = null
    useAppStore().state.feedback = null
    vi.spyOn(api, 'status').mockResolvedValue(structuredClone(statusFixture))
    vi.spyOn(api, 'triggerDatabaseUpdate').mockResolvedValue({ accepted: true })
  })
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('validates and persists units and disabled automation', async () => {
    const saveSettings = vi.fn().mockResolvedValue(undefined)
    render(DatabaseUpdatesView, { props: { settings: settingsFixture, saveSettings } })
    expect((screen.getByLabelText('Check interval') as HTMLInputElement).value).toBe('1')
    expect((screen.getByLabelText('Unit') as HTMLSelectElement).value).toBe('days')
    await fireEvent.update(screen.getByLabelText('Check interval'), '0')
    await fireEvent.click(screen.getByRole('button', { name: 'Save update settings' }))
    expect(screen.getByRole('alert')).toBeTruthy()
    expect(saveSettings).not.toHaveBeenCalled()
    await fireEvent.update(screen.getByLabelText('Check interval'), '1')
    await fireEvent.update(screen.getByLabelText('Unit'), 'hours')
    await fireEvent.click(screen.getByRole('switch'))
    await fireEvent.update(screen.getByLabelText('Check interval'), '12')
    await fireEvent.click(screen.getByRole('button', { name: 'Save update settings' }))
    expect(saveSettings.mock.calls[0][0].database_update).toEqual({
      enabled: false,
      interval_seconds: 43200,
    })
  })

  it('preserves arbitrary legacy seconds across units, save and reload', async () => {
    const settings = {
      ...settingsFixture,
      database_update: { enabled: true, interval_seconds: 61 },
    }
    const saveSettings = vi.fn().mockResolvedValue(settings)
    const view = render(DatabaseUpdatesView, { props: { settings, saveSettings } })
    for (const unit of ['days', 'hours', 'minutes']) {
      await fireEvent.update(screen.getByLabelText('Unit'), unit)
      await fireEvent.click(screen.getByRole('button', { name: 'Save update settings' }))
      expect(saveSettings.mock.lastCall?.[0].database_update.interval_seconds).toBe(61)
    }
    await view.rerender({ settings: { ...settings }, saveSettings })
    await fireEvent.click(screen.getByRole('button', { name: 'Save update settings' }))
    expect(saveSettings.mock.lastCall?.[0].database_update.interval_seconds).toBe(61)
  })

  it('loads effective settings before polling and reloads the effective saved value', async () => {
    const store = useAppStore()
    store.state.settings = null
    vi.spyOn(api, 'settings').mockResolvedValue(structuredClone(settingsFixture))
    vi.spyOn(api, 'prowlarrStatus').mockResolvedValue(statusFixture.prowlarr)
    vi.spyOn(api, 'saveSettings').mockResolvedValue({
      ...settingsFixture,
      database_update: { enabled: true, interval_seconds: 7200 },
    })
    const view = render(DatabaseUpdatesView)
    await flushPromises()
    expect(api.settings).toHaveBeenCalledTimes(1)
    expect((screen.getByLabelText('Unit') as HTMLSelectElement).value).toBe('days')
    await fireEvent.update(screen.getByLabelText('Check interval'), '2')
    await fireEvent.click(screen.getByRole('button', { name: 'Save update settings' }))
    await flushPromises()
    expect(store.state.settings).toEqual(
      expect.objectContaining({ database_update: { enabled: true, interval_seconds: 7200 } }),
    )
    expect((screen.getByLabelText('Unit') as HTMLSelectElement).value).toBe('hours')
    expect((screen.getByLabelText('Check interval') as HTMLInputElement).value).toBe('2')
    view.unmount()
    render(DatabaseUpdatesView)
    await flushPromises()
    expect((screen.getByLabelText('Unit') as HTMLSelectElement).value).toBe('hours')
    expect(api.settings).toHaveBeenCalledTimes(1)
  })

  it.each(['', '0', '-1', '0.00001', '8'])(
    'rejects invalid days %s before saving',
    async (value) => {
      const saveSettings = vi.fn()
      render(DatabaseUpdatesView, { props: { settings: settingsFixture, saveSettings } })
      await fireEvent.update(screen.getByLabelText('Check interval'), value)
      await fireEvent.click(screen.getByRole('button', { name: 'Save update settings' }))
      expect(screen.getByRole('alert')).toBeTruthy()
      expect(saveSettings).not.toHaveBeenCalled()
    },
  )

  it.each(['interval', 'enabled'])(
    'recovers missing settings after initial maintenance, preserves %s edits, and permits saving',
    async (edited) => {
      const store = useAppStore()
      store.state.settings = null
      vi.mocked(api.status).mockRejectedValueOnce(new ApiError('maintenance', 503))
      let finish!: (value: typeof settingsFixture) => void
      vi.spyOn(api, 'settings')
        .mockRejectedValueOnce(new ApiError('maintenance', 503))
        .mockImplementationOnce(
          () =>
            new Promise((resolve) => {
              finish = resolve
            }),
        )
      vi.spyOn(api, 'prowlarrStatus').mockResolvedValue(statusFixture.prowlarr)
      const save = vi.spyOn(api, 'saveSettings').mockImplementation(async (value) => value)
      render(DatabaseUpdatesView)
      await flushPromises()
      expect(api.settings).toHaveBeenCalledTimes(2)
      expect(api.prowlarrStatus).toHaveBeenCalledTimes(1)
      expect(store.state.error).toBe('maintenance')
      if (edited === 'interval')
        await fireEvent.update(screen.getByLabelText('Check interval'), '2')
      else await fireEvent.click(screen.getByRole('switch'))
      await vi.advanceTimersByTimeAsync(10000)
      expect(api.settings).toHaveBeenCalledTimes(2)
      expect(api.status).toHaveBeenCalledTimes(2)
      finish({ ...settingsFixture, database_update: { enabled: true, interval_seconds: 61 } })
      await flushPromises()
      expect(store.state.error).toBeNull()
      expect((screen.getByLabelText('Check interval') as HTMLInputElement).value).toBe(
        edited === 'interval' ? '2' : String(61 / 60),
      )
      await fireEvent.click(screen.getByRole('button', { name: 'Save update settings' }))
      await flushPromises()
      expect(save).toHaveBeenCalledWith(
        expect.objectContaining({
          database_update: {
            enabled: edited === 'interval',
            interval_seconds: edited === 'interval' ? 172800 : 61,
          },
        }),
      )
      await vi.advanceTimersByTimeAsync(4000)
      expect(api.settings).toHaveBeenCalledTimes(2)
      expect(api.prowlarrStatus).toHaveBeenCalledTimes(1)
    },
  )

  it('retries missing settings sequentially without repeated Prowlarr probes', async () => {
    const store = useAppStore()
    store.state.settings = null
    vi.mocked(api.status)
      .mockRejectedValueOnce(new ApiError('maintenance', 503))
      .mockRejectedValueOnce(new ApiError('maintenance', 503))
    vi.spyOn(api, 'settings')
      .mockRejectedValueOnce(new ApiError('maintenance', 503))
      .mockRejectedValueOnce(new ApiError('maintenance', 503))
      .mockResolvedValue(settingsFixture)
    vi.spyOn(api, 'prowlarrStatus').mockResolvedValue(statusFixture.prowlarr)
    render(DatabaseUpdatesView)
    await flushPromises()
    expect(api.settings).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(2000)
    expect(api.settings).toHaveBeenCalledTimes(2)
    expect(store.state.error).toBe('maintenance')
    await vi.advanceTimersByTimeAsync(2000)
    expect(api.settings).toHaveBeenCalledTimes(3)
    expect(store.state.settings).toEqual(settingsFixture)
    expect(store.state.error).toBeNull()
    await vi.advanceTimersByTimeAsync(4000)
    expect(api.settings).toHaveBeenCalledTimes(3)
    expect(api.prowlarrStatus).toHaveBeenCalledTimes(1)
  })

  it('aborts missing-settings recovery on unmount and ignores a late settings response', async () => {
    const store = useAppStore()
    store.state.settings = null
    vi.mocked(api.status).mockRejectedValueOnce(new ApiError('maintenance', 503))
    let finish!: (value: typeof settingsFixture) => void
    vi.spyOn(api, 'settings')
      .mockRejectedValueOnce(new ApiError('maintenance', 503))
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finish = resolve
          }),
      )
    vi.spyOn(api, 'prowlarrStatus').mockResolvedValue(statusFixture.prowlarr)
    const view = render(DatabaseUpdatesView)
    await flushPromises()
    const signal = vi.mocked(api.settings).mock.calls[1]?.[0]
    view.unmount()
    expect(signal?.aborted).toBe(true)
    finish(settingsFixture)
    await flushPromises()
    await vi.advanceTimersByTimeAsync(4000)
    expect(store.state.settings).toBeNull()
    expect(api.settings).toHaveBeenCalledTimes(2)
    expect(api.status).toHaveBeenCalledTimes(2)
  })

  it('polls status only, preserves edits and recovers after maintenance', async () => {
    vi.mocked(api.status)
      .mockResolvedValueOnce({
        ...statusFixture,
        updater: { ...statusFixture.updater, phase: 'checking', updating: true },
      })
      .mockRejectedValueOnce(new ApiError('maintenance', 503))
      .mockResolvedValue({
        ...statusFixture,
        updater: { ...statusFixture.updater, phase: 'up_to_date' },
      })
    render(DatabaseUpdatesView, { props: { settings: settingsFixture } })
    await flushPromises()
    expect(screen.getByText('Checking the available snapshot…')).toBeTruthy()
    expect(
      (screen.getByRole('button', { name: 'Check and update now' }) as HTMLButtonElement).disabled,
    ).toBe(true)
    await fireEvent.update(screen.getByLabelText('Check interval'), '2')
    await vi.advanceTimersByTimeAsync(2000)
    expect(screen.queryByText('maintenance')).toBeNull()
    await vi.advanceTimersByTimeAsync(2000)
    expect(screen.getByText('The database is already up to date.')).toBeTruthy()
    expect((screen.getByLabelText('Check interval') as HTMLInputElement).value).toBe('2')
  })

  it('accepts one manual request with automation off, then shows completion', async () => {
    const settings = {
      ...settingsFixture,
      database_update: { enabled: false, interval_seconds: 86400 },
    }
    render(DatabaseUpdatesView, { props: { settings } })
    await flushPromises()
    const button = screen.getByRole('button', { name: 'Check and update now' })
    await fireEvent.click(button)
    await flushPromises()
    await fireEvent.click(button)
    expect(api.triggerDatabaseUpdate).toHaveBeenCalledTimes(1)
    expect(screen.getByText('Request accepted. Checking for updates.')).toBeTruthy()
    vi.mocked(api.status).mockResolvedValue({
      ...statusFixture,
      updater: { ...statusFixture.updater, phase: 'updated' },
    })
    await vi.advanceTimersByTimeAsync(2000)
    expect(screen.getByText('Database updated.')).toBeTruthy()
    expect((button as HTMLButtonElement).disabled).toBe(false)
  })

  it('clears polling errors on recovery without hiding the terminal result', async () => {
    vi.mocked(api.status)
      .mockRejectedValueOnce(new ApiError('offline'))
      .mockResolvedValue({
        ...statusFixture,
        updater: { ...statusFixture.updater, phase: 'updated' },
      })
    render(DatabaseUpdatesView, { props: { settings: settingsFixture } })
    await flushPromises()
    expect(screen.getByRole('alert').textContent).toBe('offline')
    await vi.advanceTimersByTimeAsync(2000)
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.getByText('Database updated.')).toBeTruthy()
  })

  it.each([409, 500])('shows manual request errors %s', async (status) => {
    vi.mocked(api.triggerDatabaseUpdate).mockRejectedValue(new ApiError('request failed', status))
    render(DatabaseUpdatesView, { props: { settings: settingsFixture } })
    await flushPromises()
    await fireEvent.click(screen.getByRole('button', { name: 'Check and update now' }))
    await flushPromises()
    expect(
      screen.getByText(
        status === 409 ? 'A check or update is already in progress.' : 'request failed',
      ),
    ).toBeTruthy()
  })

  it('does not overlap polls, aborts on unmount, and ignores late results', async () => {
    let finish!: (value: typeof statusFixture) => void
    vi.mocked(api.status).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve
        }),
    )
    const view = render(DatabaseUpdatesView, { props: { settings: settingsFixture } })
    await vi.advanceTimersByTimeAsync(10000)
    expect(api.status).toHaveBeenCalledTimes(1)
    const signal = vi.mocked(api.status).mock.calls[0]?.[0]
    view.unmount()
    expect(signal?.aborted).toBe(true)
    finish(statusFixture)
    await flushPromises()
    await vi.advanceTimersByTimeAsync(10000)
    expect(api.status).toHaveBeenCalledTimes(1)
    expect(useAppStore().state.status).toBeNull()
  })

  it('shows localized failure feedback', async () => {
    useLocale().setLocale('it')
    vi.mocked(api.status).mockResolvedValue({
      ...statusFixture,
      updater: { ...statusFixture.updater, phase: 'failed' },
    })
    render(DatabaseUpdatesView, { props: { settings: settingsFixture } })
    await flushPromises()
    expect(screen.getByText('Aggiornamento del database non riuscito.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Controlla e aggiorna ora' })).toBeTruthy()
  })
})

describe('exact interval conversion', () => {
  it.each([
    ['1', 'minutes', 60],
    ['7', 'days', 604800],
    ['1.5', 'minutes', 90],
    ['0.025', 'hours', 90],
    ['.5', 'hours', 1800],
    ['0.001', 'days', null],
    ['', 'minutes', null],
    ['Infinity', 'hours', null],
    ['NaN', 'days', null],
    ['word', 'hours', null],
    ['-1', 'minutes', null],
    ['0', 'hours', null],
    ['0.99', 'minutes', null],
    ['7.1', 'days', null],
    ['1e0', 'minutes', 60],
    ['1.00000000000000001', 'minutes', null],
  ] as const)('converts %s %s to %s', (value, unit, expected) => {
    expect(intervalSeconds(value, unit)).toBe(expected)
  })
})
