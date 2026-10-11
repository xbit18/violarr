import { fireEvent, render, screen, within } from '@testing-library/vue'
import { flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { router } from '../router'
import DiagnosticsView from '../views/DiagnosticsView.vue'
import { useLocale } from '../i18n'
import {
  exportReport,
  exportHistory,
  type SearchReport,
  type MonitoredRequest,
} from '../api/diagnostics'

function report() {
  return {
    report_version: 1,
    application_version: '1.2.3',
    snapshot_version: 'snapshot-1',
    generated_at: '2026-10-10T12:00:00Z',
    original: { t: 'tvsearch', q: 'Sensitive series', limit: 100, offset: 0 },
    normalized: { t: 'tvsearch', q: 'Sensitive series', limit: 100, offset: 0 },
    strategy: 'tv_title',
    stages: Object.fromEntries(
      ['input', 'database', 'processing', 'serialization'].map((name) => [
        name,
        { status: 'success', duration_ms: 2 },
      ]),
    ),
    counts: { candidates: 3, excluded: 1, retained: 2, outside_page: 1, selected: 1, returned: 1 },
    windows: [{ offset: 0, limit: 1000, candidates: 3 }],
    processing: {
      preset: 'custom',
      subtitle_language_correction: true,
      custom_rule_count: 1,
      custom_rules: [
        {
          index: 0,
          enabled: true,
          field: 'title',
          operator: 'contains',
          value: 'CAM',
          action: 'exclude',
          score: null,
        },
      ],
    },
    serialization_settings: { subtitle_language_correction: true },
    releases: [
      {
        id: '0:0',
        title: 'Sensitive series CAM',
        size: 100,
        seeders: 2,
        provider: 'Provider',
        type: 'tv',
        category: 5000,
        language: null,
        subtitle_corrected_for_processing: false,
        status: 'excluded',
        reason: 'custom_rule:0',
        score: 0,
        score_rules: [],
      },
      {
        id: '0:1',
        title: 'Sensitive series SUB ITA',
        size: 200,
        seeders: 3,
        provider: 'Provider',
        type: 'tv',
        category: 5000,
        language: 'English',
        subtitle_corrected_for_processing: true,
        status: 'returned',
        reason: null,
        score: 10,
        score_rules: [0],
      },
      {
        id: '0:2',
        title: 'Outside page',
        size: null,
        seeders: null,
        provider: null,
        type: 'tv',
        category: 5000,
        language: null,
        subtitle_corrected_for_processing: false,
        status: 'outside_page',
        reason: null,
        score: 0,
        score_rules: [],
      },
    ],
    errors: [],
    duration_ms: 8,
    truncated: false,
    replayable: true,
    limitations: ['Window-local ranking.'],
  }
}
function entry(replayable = true) {
  const r = report()
  return {
    id: 1,
    timestamp: r.generated_at,
    original: {
      t: 'tvsearch',
      q: 'Original query',
      imdbid: 'tt123',
      season: 2,
      ep: 3,
      cat: '5000',
      tmdbid: 99,
      limit: 20,
      offset: 5,
    },
    normalized: { q: 'Different normalized query' },
    strategy: r.strategy,
    stages: r.stages,
    counts: r.counts,
    duration_ms: 8,
    status_code: 200,
    errors: [],
    truncated: !replayable,
    replayable,
  }
}
function reportV2() {
  return {
    ...report(),
    report_version: 2,
    stages: {
      ...report().stages,
      search: { status: 'success', duration_ms: 3 },
      merge: { status: 'success', duration_ms: 1 },
    },
    strategies: [
      {
        identifier: 'synthetic_exact',
        status: 'success',
        duration_ms: 2,
        candidates: 2,
        unique_contribution: null,
        metadata: { field: 'imdb', sql: 'secret' },
      },
      {
        identifier: 'synthetic_title',
        status: 'partial',
        duration_ms: 1,
        candidates: 1,
        unique_contribution: null,
        metadata: { match_type: 'contains' },
      },
    ],
    releases: report().releases.map((item) => ({ ...item, identity: 'c'.repeat(64) })),
    provenance: [
      {
        identity: 'c'.repeat(64),
        occurrences: ['0:0', '0:1'],
        strategies: ['synthetic_exact', 'synthetic_title'],
        match_evidence: [{ strategy: 'synthetic_exact', field: 'imdb', token: 'secret' }],
        deduplicated: null,
        relevance: null,
        included: null,
        info_hash: 'a'.repeat(40),
      },
    ],
  }
}
const fetchMock = vi.fn()
let enabled = false
let history = [entry()]
function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status })
}
function mount() {
  return render(DiagnosticsView, { global: { plugins: [router] } })
}
async function run() {
  await fireEvent.click(screen.getByRole('button', { name: 'Run diagnostics' }))
  await flushPromises()
}
async function requests() {
  await fireEvent.click(screen.getByRole('button', { name: 'Requests' }))
  await flushPromises()
}

describe('diagnostics', () => {
  it('exports optional numeric fuzzy pool counts in reports and history without private fields', () => {
    const r = reportV2()
    const pool = { found: 2001, retained: 2000, omitted: 1, truncated: true }
    const strategies = [
      {
        ...r.strategies[0],
        fuzzy_pool: { ...pool, info_hash: 'private', confidence: 0.9, file_path: '/private' },
      },
    ]
    const exported = JSON.parse(exportReport({ ...r, strategies } as unknown as SearchReport))
    expect(exported.strategies[0].fuzzy_pool).toEqual(pool)
    const history = JSON.parse(
      exportHistory([{ ...entry(), strategies } as unknown as MonitoredRequest]),
    )
    expect(history.requests[0].strategies[0].fuzzy_pool).toEqual(pool)
    const malformed = [
      {
        ...r.strategies[0],
        fuzzy_pool: { found: { token: 'secret' }, retained: -1, omitted: 1.5, truncated: 'yes' },
      },
    ]
    expect(
      JSON.parse(exportReport({ ...r, strategies: malformed } as unknown as SearchReport))
        .strategies[0].fuzzy_pool,
    ).toEqual({})
    expect(exportReport({ ...r, strategies } as unknown as SearchReport)).not.toContain('private')
    expect(
      JSON.parse(exportReport(r as unknown as SearchReport)).strategies[0].fuzzy_pool,
    ).toBeUndefined()
  })

  it.each([report, reportV2])(
    'rejects malformed export array leaves in both report versions',
    (fixture) => {
      const valid = fixture()
      const malformed = {
        ...valid,
        releases: valid.releases.map((release) => ({
          ...release,
          score_rules: [
            { api_key: 'LEAF_SECRET_123' },
            [2],
            '3',
            true,
            null,
            NaN,
            Infinity,
            1.5,
            0,
            2,
          ],
        })),
        limitations: [
          { credentials: 'LIMIT_SECRET_123' },
          ['nested'],
          null,
          1,
          'Window-local ranking.',
        ],
      }
      const exported = exportReport(malformed as unknown as SearchReport)
      const parsed = JSON.parse(exported)
      expect(exported).not.toContain('LEAF_SECRET_123')
      expect(exported).not.toContain('LIMIT_SECRET_123')
      expect(parsed.releases[0].score_rules).toEqual([0, 2])
      expect(parsed.limitations).toEqual(['Window-local ranking.'])
      const preserved = JSON.parse(exportReport(valid as unknown as SearchReport))
      expect(
        preserved.releases.map((release: { score_rules: number[] }) => release.score_rules),
      ).toEqual(valid.releases.map((release) => release.score_rules))
      expect(preserved.limitations).toEqual(valid.limitations)
    },
  )

  beforeEach(() => {
    useLocale().setLocale('en')
    enabled = false
    history = [entry()]
    fetchMock.mockReset().mockImplementation((path: string, init?: RequestInit) => {
      if (path.endsWith('/diagnostics/search')) return Promise.resolve(json(report()))
      if (path.endsWith('/diagnostics/monitoring')) {
        if (init?.method === 'PUT') enabled = JSON.parse(String(init.body)).enabled
        return Promise.resolve(json({ enabled, capacity: 100, count: history.length }))
      }
      if (path.endsWith('/diagnostics/requests')) {
        if (init?.method === 'DELETE') history = []
        return Promise.resolve(json({ requests: history }))
      }
      return Promise.resolve(json({}))
    })
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('submits TV TMDB together with IMDb and episode parameters', async () => {
    mount()
    await fireEvent.update(screen.getByRole('combobox'), 'tvsearch')
    await fireEvent.click(screen.getByRole('button', { name: 'Advanced parameters' }))
    await fireEvent.update(screen.getByLabelText('IMDb ID'), 'tt26545971')
    await fireEvent.update(screen.getByLabelText('TMDb ID'), '273419')
    await fireEvent.update(screen.getByLabelText('Season'), '1')
    await fireEvent.update(screen.getByLabelText('Episode'), '2')
    await run()
    const call = fetchMock.mock.calls.find(([path]) => path.endsWith('/diagnostics/search'))!
    expect(JSON.parse(call[1].body)).toMatchObject({
      t: 'tvsearch',
      imdbid: 'tt26545971',
      tmdbid: 273419,
      season: 1,
      ep: 2,
    })
  })

  it('renders and exports unavailable independent strategy timings and safe source evidence', async () => {
    const r = reportV2()
    const merged = {
      ...r,
      strategies: r.strategies.map((s) => ({
        ...s,
        duration_ms: null,
        metadata: { field: 'imdb', match_type: 'exact', source: 'file' },
      })),
    }
    fetchMock.mockResolvedValue(json(merged))
    mount()
    await run()
    expect(screen.getByText('synthetic_exact')).toBeTruthy()
    const exported = JSON.parse(exportReport(merged as unknown as SearchReport))
    expect(exported.strategies[0].duration_ms).toBeNull()
    expect(exported.strategies[0].metadata.source).toBe('file')
  })

  it('renders and exports fuzzy pack evidence without child identifiers or paths', async () => {
    const r = reportV2()
    const metadata = { field: 'title', match_type: 'fuzzy', source: 'pack_file' }
    const covered = {
      ...r,
      strategies: [
        { ...r.strategies[0], identifier: 'movie_pack_title_fuzzy', duration_ms: null, metadata },
      ],
      provenance: [
        {
          ...r.provenance[0],
          match_evidence: [
            {
              strategy: 'movie_pack_title_fuzzy',
              ...metadata,
              file_path: '/private/child.mkv',
              imdb_id: 'private-child-id',
            },
          ],
        },
      ],
    }
    fetchMock.mockResolvedValue(json(covered))
    mount()
    await run()
    expect(screen.getByText('movie_pack_title_fuzzy')).toBeTruthy()
    const serialized = exportReport(covered as unknown as SearchReport)
    const projected = JSON.parse(serialized)
    expect(projected.strategies[0].metadata).toEqual(metadata)
    expect(projected.provenance[0].match_evidence[0]).toEqual({
      strategy: 'movie_pack_title_fuzzy',
      ...metadata,
    })
    expect(serialized).not.toContain('private-child-id')
    expect(serialized).not.toContain('child.mkv')
  })

  it('renders v2 strategy executions, optional phases and unavailable provenance facts', async () => {
    fetchMock.mockResolvedValue(json(reportV2()))
    mount()
    await run()
    expect(screen.getByRole('heading', { name: 'Search execution' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Merge observations' })).toBeTruthy()
    expect(screen.getByText('synthetic_exact')).toBeTruthy()
    expect(screen.getByText('synthetic_title')).toBeTruthy()
    expect(screen.getByText('Partial')).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Result provenance' })).toBeTruthy()
    expect(screen.getByText(/synthetic_exact · imdb/)).toBeTruthy()
    expect(screen.getByText(/Deduplicated: Not available/)).toBeTruthy()
    expect(screen.queryByText('secret')).toBeNull()
  })

  it('shows partial strategy execution in request history without changing replay', async () => {
    const r = reportV2()
    history = [
      { ...entry(), strategies: r.strategies, stages: r.stages } as (typeof history)[number],
    ]
    mount()
    await requests()
    expect(screen.getByText('The diagnostic pipeline did not complete.')).toBeTruthy()
    await fireEvent.click(screen.getByRole('button', { name: 'Inspect request 1' }))
    expect(screen.getByText(/synthetic_title · Partial/)).toBeTruthy()
    expect(
      (screen.getByRole('button', { name: 'Replay request' }) as HTMLButtonElement).disabled,
    ).toBe(false)
  })

  it('exports v2 provenance and strategies with scalar allowlists, retains v1 export and independent history version', () => {
    const r = reportV2()
    r.original.q = `hash ${'a'.repeat(40)} /Users/private token=secret`
    const exported = exportReport(r as unknown as SearchReport)
    const parsed = JSON.parse(exported)
    expect(parsed.report_version).toBe(2)
    expect(parsed.strategies).toHaveLength(2)
    expect(parsed.strategies[1].status).toBe('partial')
    expect(parsed.provenance[0].identity).toBe('c'.repeat(64))
    expect(parsed.provenance[0].relevance).toBeNull()
    expect(parsed.stages.merge.status).toBe('success')
    expect(parsed.releases[0].identity).toBe('c'.repeat(64))
    expect(exported).not.toContain('secret')
    expect(exported).not.toContain('a'.repeat(40))
    expect(exported).not.toContain('info_hash')
    const unsafeMetadata = {
      ...r,
      strategies: [
        {
          ...r.strategies[0],
          metadata: { field: 'SELECT private', match_type: 'password=private' },
        },
      ],
    }
    expect(exportReport(unsafeMetadata as unknown as SearchReport)).not.toContain('SELECT private')
    const malicious = {
      ...r,
      strategies: [{ ...r.strategies[0], metadata: { field: { password: 'nested-secret' } } }],
    }
    expect(exportReport(malicious as unknown as SearchReport)).not.toContain('nested-secret')
    expect(JSON.parse(exportReport(report() as unknown as SearchReport)).report_version).toBe(1)
    const historyText = exportHistory([
      { ...entry(), strategies: r.strategies, stages: r.stages } as unknown as MonitoredRequest,
    ])
    const history = JSON.parse(historyText)
    expect(history.export_version).toBe(1)
    expect(history.requests[0].strategies).toHaveLength(2)
    expect(history.requests[0].stages.search.status).toBe('success')
    expect(historyText).not.toContain('secret')
    expect(history.requests[0].provenance).toBeUndefined()
  })

  it('registers a native route with Search as default and explicit no-report state', () => {
    mount()
    expect(router.resolve('/diagnostics').matched).toHaveLength(1)
    expect(screen.getByRole('heading', { name: 'Diagnostics' })).toBeTruthy()
    expect(screen.getByText('Run a search to create a diagnostic report.')).toBeTruthy()
    expect(
      (screen.getByRole('button', { name: 'Export report' }) as HTMLButtonElement).disabled,
    ).toBe(true)
  })

  it('shows conditional advanced inputs and sends strict numeric payloads without stale hidden fields', async () => {
    mount()
    expect(screen.queryByLabelText('IMDb ID')).toBeNull()
    await fireEvent.click(screen.getByRole('button', { name: 'Advanced parameters' }))
    expect(screen.queryByLabelText('Season')).toBeNull()
    await fireEvent.update(screen.getByLabelText('Search type'), 'tvsearch')
    expect(screen.getByLabelText('TMDb ID')).toBeTruthy()
    await fireEvent.update(screen.getByLabelText('Season'), '2')
    await fireEvent.update(screen.getByLabelText('Episode'), '3')
    await fireEvent.update(screen.getByLabelText('Search query'), '  title  ')
    await fireEvent.update(screen.getByLabelText('Limit'), '20')
    await fireEvent.update(screen.getByLabelText('Offset'), '5')
    await run()
    expect(JSON.parse(fetchMock.mock.calls.find(([p]) => p.endsWith('/search'))![1].body)).toEqual({
      t: 'tvsearch',
      q: '  title  ',
      imdbid: null,
      tmdbid: null,
      season: 2,
      ep: 3,
      cat: null,
      limit: 20,
      offset: 5,
    })
    await fireEvent.update(screen.getByLabelText('Search type'), 'movie')
    expect(screen.queryByLabelText('Season')).toBeNull()
    expect(screen.getByLabelText('TMDb ID')).toBeTruthy()
    await run()
    expect(
      JSON.parse(fetchMock.mock.calls.filter(([p]) => p.endsWith('/search')).at(-1)![1].body)
        .season,
    ).toBeNull()
  })

  it('rejects out-of-range and fractional numbers before making a search request', async () => {
    mount()
    await fireEvent.click(screen.getByRole('button', { name: 'Advanced parameters' }))
    await fireEvent.update(screen.getByLabelText('Limit'), '201')
    await run()
    expect(screen.getByRole('alert').textContent).toContain('valid parameters')
    expect(fetchMock.mock.calls.some(([p]) => p.endsWith('/search'))).toBe(false)
    await fireEvent.update(screen.getByLabelText('Limit'), '10.5')
    await run()
    expect(fetchMock.mock.calls.some(([p]) => p.endsWith('/search'))).toBe(false)
  })

  it('renders truthful stages, window counts, settings and candidate details', async () => {
    mount()
    await run()
    expect(screen.getByText(/Counts describe inspected database windows/)).toBeTruthy()
    expect(screen.getByText(/Downstream acceptance is unknown/)).toBeTruthy()
    expect(screen.getByText('tv_title')).toBeTruthy()
    expect(screen.getByText('custom_rule:0')).toBeTruthy()
    expect(screen.getByText('Sensitive series SUB ITA')).toBeTruthy()
    expect(screen.getByText('tv / 5000 / English')).toBeTruthy()
    expect(screen.getByText('Outside final page')).toBeTruthy()
    expect(screen.getByText('Subtitle correction applied for processing')).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Torznab serialization' })).toBeTruthy()
  })

  it('does not imply success for HTTP 200 reports with failed serialization', async () => {
    const r = report()
    r.stages.serialization!.status = 'failed'
    r.counts.returned = 0
    r.releases[1]!.status = 'selected'
    fetchMock.mockImplementation((p: string) =>
      Promise.resolve(
        json(
          p.endsWith('/search')
            ? {
                ...r,
                errors: [
                  {
                    stage: 'serialization',
                    code: 'serialization_failed',
                    message: 'Torznab serialization failed',
                  },
                ],
              }
            : {},
        ),
      ),
    )
    mount()
    await run()
    expect(screen.getByText('The diagnostic pipeline did not complete.')).toBeTruthy()
    expect(screen.getByText('Selected, not confirmed returned')).toBeTruthy()
    expect(screen.queryByText(/Violarr generated a validated/)).toBeNull()
  })

  it('handles no candidates without claiming absence from the whole database', async () => {
    fetchMock.mockResolvedValue(
      json({
        ...report(),
        releases: [],
        counts: {
          candidates: 0,
          excluded: 0,
          retained: 0,
          outside_page: 0,
          selected: 0,
          returned: 0,
        },
      }),
    )
    mount()
    await run()
    expect(
      screen.getByText('No releases were retrieved from the inspected local database windows.'),
    ).toBeTruthy()
  })

  it('handles maintenance with no stale report and a disabled export', async () => {
    mount()
    await run()
    fetchMock.mockResolvedValue(json({ detail: 'Database maintenance' }, 503))
    await run()
    expect(screen.getByRole('alert').textContent).toContain('maintenance')
    expect(
      (screen.getByRole('button', { name: 'Export report' }) as HTMLButtonElement).disabled,
    ).toBe(true)
    expect(screen.queryByText('Sensitive series SUB ITA')).toBeNull()
  })

  it('toggles monitoring, refreshes, inspects original data and clears history', async () => {
    mount()
    await requests()
    expect(screen.getByText(/Monitoring is disabled/)).toBeTruthy()
    await fireEvent.click(screen.getByRole('switch', { name: 'Monitor incoming searches' }))
    await flushPromises()
    expect(enabled).toBe(true)
    await fireEvent.click(screen.getByRole('button', { name: 'Inspect request 1' }))
    expect(screen.getByText(/Original query/, { selector: 'pre' })).toBeTruthy()
    expect(screen.getByText(/Different normalized query/)).toBeTruthy()
    await fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    await flushPromises()
    await fireEvent.click(screen.getByRole('button', { name: 'Clear history' }))
    await flushPromises()
    expect(screen.getByText('No requests recorded.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Replay request' })).toBeNull()
  })

  it('toggles inspected details in their request card when reopening, switching and replaying', async () => {
    const first = entry()
    const second = {
      ...entry(),
      id: 2,
      original: { ...entry().original, q: 'Second original query' },
      normalized: { q: 'Second normalized query' },
    }
    history = [first, second]
    mount()
    await requests()
    const firstButton = screen.getByRole('button', { name: 'Inspect request 1' })
    const secondButton = screen.getByRole('button', { name: 'Inspect request 2' })
    const firstCard = firstButton.closest('article')!
    const secondCard = secondButton.closest('article')!

    await fireEvent.click(firstButton)
    expect.soft(within(firstCard).queryByRole('heading', { name: 'Request details' })).toBeTruthy()
    expect.soft(within(firstCard).queryByText(/Original query/, { selector: 'pre' })).toBeTruthy()
    expect.soft(within(firstCard).queryByText(/Different normalized query/)).toBeTruthy()
    expect.soft(within(secondCard).queryByRole('heading', { name: 'Request details' })).toBeNull()
    expect.soft(screen.getAllByRole('heading', { name: 'Request details' })).toHaveLength(1)

    await fireEvent.click(firstButton)
    expect.soft(screen.queryByRole('heading', { name: 'Request details' })).toBeNull()
    expect.soft(within(firstCard).queryByText(/Original query/, { selector: 'pre' })).toBeNull()
    expect.soft(within(firstCard).queryByText(/Different normalized query/)).toBeNull()
    expect.soft(screen.queryByRole('button', { name: 'Replay request' })).toBeNull()

    await fireEvent.click(firstButton)
    expect.soft(within(firstCard).queryByRole('heading', { name: 'Request details' })).toBeTruthy()
    expect.soft(within(firstCard).queryByText(/Original query/, { selector: 'pre' })).toBeTruthy()
    expect.soft(within(firstCard).queryByRole('button', { name: 'Replay request' })).toBeTruthy()
    expect.soft(within(secondCard).queryByRole('heading', { name: 'Request details' })).toBeNull()
    expect.soft(screen.getAllByRole('heading', { name: 'Request details' })).toHaveLength(1)

    await fireEvent.click(secondButton)
    expect.soft(within(firstCard).queryByRole('heading', { name: 'Request details' })).toBeNull()
    expect.soft(within(firstCard).queryByText(/Original query/, { selector: 'pre' })).toBeNull()
    expect.soft(within(firstCard).queryByRole('button', { name: 'Replay request' })).toBeNull()
    expect.soft(within(secondCard).queryByRole('heading', { name: 'Request details' })).toBeTruthy()
    expect
      .soft(within(secondCard).queryByText(/Second original query/, { selector: 'pre' }))
      .toBeTruthy()
    expect.soft(within(secondCard).queryByText(/Second normalized query/)).toBeTruthy()
    expect.soft(screen.getAllByRole('heading', { name: 'Request details' })).toHaveLength(1)
    expect.soft(screen.getAllByRole('button', { name: 'Replay request' })).toHaveLength(1)
    expect.soft(within(secondCard).queryByRole('button', { name: 'Replay request' })).toBeTruthy()

    await fireEvent.click(screen.getByRole('button', { name: 'Replay request' }))
    await flushPromises()
    const call = fetchMock.mock.calls.find(([p]) => p.endsWith('/search'))!
    expect(call[1].method).toBe('POST')
    expect(JSON.parse(call[1].body)).toEqual(second.original)
    expect(screen.getByText(/Replay uses the current database and settings/)).toBeTruthy()
    expect(screen.getByText('Sensitive series SUB ITA')).toBeTruthy()
  })

  it('replays original parameters as a fresh POST with current-state notice', async () => {
    mount()
    await requests()
    await fireEvent.click(screen.getByRole('button', { name: 'Inspect request 1' }))
    await fireEvent.click(screen.getByRole('button', { name: 'Replay request' }))
    await flushPromises()
    expect(screen.getByText(/Replay uses the current database and settings/)).toBeTruthy()
    const call = fetchMock.mock.calls.find(([p]) => p.endsWith('/search'))!
    expect(call[1].method).toBe('POST')
    expect(JSON.parse(call[1].body)).toEqual(entry().original)
    expect(screen.getByText('Sensitive series SUB ITA')).toBeTruthy()
    expect(
      fetchMock.mock.calls.some(([p, init]) => p.endsWith('/requests') && init?.method === 'POST'),
    ).toBe(false)
  })

  it('disables replay for truncated non-replayable requests', async () => {
    history = [entry(false)]
    mount()
    await requests()
    await fireEvent.click(screen.getByRole('button', { name: 'Inspect request 1' }))
    expect(
      (screen.getByRole('button', { name: 'Replay request' }) as HTMLButtonElement).disabled,
    ).toBe(true)
    expect(screen.getByText(/cannot be replayed/)).toBeTruthy()
  })

  it('previews sensitive content before bounded local JSON download and revokes the URL', async () => {
    const r = report()
    fetchMock.mockResolvedValue(
      json({
        ...r,
        password: 'secret',
        releases: r.releases.map((x) => ({ ...x, magnet: 'magnet:?secret' })),
      }),
    )
    const create = vi.fn().mockReturnValue('blob:report')
    const revoke = vi.fn()
    vi.stubGlobal('URL', { createObjectURL: create, revokeObjectURL: revoke })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    mount()
    await run()
    await fireEvent.click(screen.getByRole('button', { name: 'Export report' }))
    const preview = screen.getByRole('region', { name: 'Report preview' })
    expect(
      within(preview).getByText(/Search terms and release titles may be sensitive/),
    ).toBeTruthy()
    expect(preview.textContent).toContain('Sensitive series')
    expect(preview.textContent).not.toContain('secret')
    expect(click).not.toHaveBeenCalled()
    await fireEvent.click(within(preview).getByRole('button', { name: 'Download JSON' }))
    expect(create).toHaveBeenCalledOnce()
    expect(create.mock.calls[0]![0].size).toBeLessThan(4_000_000)
    expect(click).toHaveBeenCalledOnce()
    expect(revoke).toHaveBeenCalledWith('blob:report')
  })

  it('exports fresh full history metadata, not just the inspected entry, after privacy confirmation', async () => {
    const create = vi.fn().mockReturnValue('blob:history')
    const revoke = vi.fn()
    vi.stubGlobal('URL', { createObjectURL: create, revokeObjectURL: revoke })
    let filename = ''
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      filename = this.download
    })
    mount()
    await requests()
    await fireEvent.click(screen.getByRole('button', { name: 'Inspect request 1' }))
    const button = screen.getByRole('button', { name: 'Export history' })
    expect(screen.getByRole('button', { name: 'Clear history' }).nextElementSibling).toBe(button)
    const second = { ...entry(false), id: 2, status_code: 503, duration_ms: 19 }
    history = [entry(), second]
    await fireEvent.click(button)
    await flushPromises()
    const preview = screen.getByRole('region', { name: 'History preview' })
    expect(
      within(preview).getByText(/Search terms and release titles may be sensitive/),
    ).toBeTruthy()
    const exported = JSON.parse(preview.querySelector('pre')!.textContent!)
    expect(exported.export_version).toBe(1)
    expect(exported.kind).toBe('monitored_requests')
    expect(Number.isNaN(Date.parse(exported.generated_at))).toBe(false)
    expect(exported.requests).toEqual(history)
    expect(click).not.toHaveBeenCalled()
    await fireEvent.click(within(preview).getByRole('button', { name: 'Download JSON' }))
    expect(filename).toBe('violarr-request-history-v1.json')
    expect(create.mock.calls[0]![0].type).toBe('application/json')
    const downloaded = await new Promise<string>((resolve) => {
      const reader = new FileReader()
      reader.onload = () => resolve(String(reader.result))
      reader.readAsText(create.mock.calls[0]![0])
    })
    expect(JSON.parse(downloaded)).toEqual(exported)
    expect(click).toHaveBeenCalledOnce()
    expect(revoke).toHaveBeenCalledWith('blob:history')
  })

  it('disables empty and pending history exports, reports fetch errors without stale downloads', async () => {
    history = []
    mount()
    await requests()
    const button = screen.getByRole('button', { name: 'Export history' }) as HTMLButtonElement
    expect(button.disabled).toBe(true)
    history = [entry()]
    await fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    await flushPromises()
    let reject!: (cause: unknown) => void
    fetchMock.mockImplementation(
      () =>
        new Promise<Response>((_, r) => {
          reject = r
        }),
    )
    await fireEvent.click(button)
    expect(button.disabled).toBe(true)
    expect(
      (screen.getByRole('button', { name: 'Clear history' }) as HTMLButtonElement).disabled,
    ).toBe(true)
    reject(new TypeError('offline'))
    await flushPromises()
    expect(screen.getByRole('alert')).toBeTruthy()
    expect(screen.queryByRole('region', { name: 'History preview' })).toBeNull()
    expect(button.disabled).toBe(false)
  })

  it('projects and sanitizes every history entry and revokes URLs even on download failure', async () => {
    mount()
    await requests()
    const malicious = {
      ...entry(),
      original: {
        ...entry().original,
        q: 'Sensitive /Users/private magnet:?xt=secret token=secret',
        apikey: 'secret',
      },
      normalized: { q: { password: 'secret' } },
      stages: {
        ...entry().stages,
        input: { status: 'success', duration_ms: 2, sql: 'secret' },
        secret: 'secret',
      },
      counts: { ...entry().counts, password: 'secret' },
      errors: [
        { stage: 'database', code: 'failed', message: 'password=secret', traceback: 'secret' },
      ],
      credentials: 'secret',
    }
    fetchMock.mockResolvedValue(json({ requests: [malicious, { ...malicious, id: 2 }] }))
    const create = vi.fn().mockReturnValue('blob:history')
    const revoke = vi.fn()
    vi.stubGlobal('URL', { createObjectURL: create, revokeObjectURL: revoke })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {
      throw new Error('blocked')
    })
    await fireEvent.click(screen.getByRole('button', { name: 'Export history' }))
    await flushPromises()
    const preview = screen.getByRole('region', { name: 'History preview' })
    const text = preview.querySelector('pre')!.textContent!
    expect(JSON.parse(text).requests).toHaveLength(2)
    expect(text).toContain('Sensitive')
    expect(text).toContain('[redacted]')
    for (const forbidden of [
      'secret',
      '/Users/private',
      'magnet:',
      'apikey',
      'credentials',
      'traceback',
    ])
      expect(text).not.toContain(forbidden)
    await fireEvent.click(within(preview).getByRole('button', { name: 'Download JSON' }))
    expect(screen.getByRole('alert').textContent).toContain('export')
    expect(revoke).toHaveBeenCalledWith('blob:history')
  })

  it('exports all 100 buffered entries without changing monitoring or inspection', async () => {
    mount()
    await requests()
    await fireEvent.click(screen.getByRole('button', { name: 'Inspect request 1' }))
    history = Array.from({ length: 100 }, (_, index) => ({ ...entry(), id: index + 1 }))
    await fireEvent.click(screen.getByRole('button', { name: 'Export history' }))
    await flushPromises()
    const preview = screen.getByRole('region', { name: 'History preview' })
    expect(JSON.parse(preview.querySelector('pre')!.textContent!).requests).toEqual(history)
    expect(screen.getByRole('heading', { name: 'Request details' })).toBeTruthy()
    expect(enabled).toBe(false)
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method)).toHaveLength(0)
  })

  it('aborts pending export on unmount and ignores its late response without restarting polling', async () => {
    vi.useFakeTimers()
    const view = mount()
    await requests()
    let resolve!: (value: Response) => void
    let signal: AbortSignal | undefined
    fetchMock.mockImplementation((_: string, init?: RequestInit) => {
      signal = init?.signal as AbortSignal
      return new Promise<Response>((r) => {
        resolve = r
      })
    })
    await fireEvent.click(screen.getByRole('button', { name: 'Export history' }))
    view.unmount()
    expect(signal?.aborted).toBe(true)
    resolve(json({ requests: [entry()] }))
    await flushPromises()
    expect(vi.getTimerCount()).toBe(0)
    expect(screen.queryByRole('region', { name: 'History preview' })).toBeNull()
  })

  it('shows loading, prevents overlapping searches and clears polling on navigation/unmount', async () => {
    vi.useFakeTimers()
    const view = mount()
    await requests()
    const calls = fetchMock.mock.calls.length
    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    expect(fetchMock.mock.calls.length).toBeGreaterThan(calls)
    await fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    const stopped = fetchMock.mock.calls.length
    await vi.advanceTimersByTimeAsync(5000)
    expect(fetchMock.mock.calls.length).toBe(stopped)
    let resolve!: (value: Response) => void
    fetchMock.mockImplementation(
      () =>
        new Promise<Response>((r) => {
          resolve = r
        }),
    )
    await fireEvent.click(screen.getByRole('button', { name: 'Run diagnostics' }))
    expect(screen.getByText('Running diagnostics…')).toBeTruthy()
    expect(
      (screen.getByRole('button', { name: 'Run diagnostics' }) as HTMLButtonElement).disabled,
    ).toBe(true)
    view.unmount()
    resolve(json(report()))
    await flushPromises()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('does not resurrect cleared history from a stale poll', async () => {
    vi.useFakeTimers()
    mount()
    await requests()
    let resolve!: (value: Response) => void
    fetchMock.mockImplementation((p: string, init?: RequestInit) => {
      if (init?.method === 'DELETE') return Promise.resolve(json({ requests: [] }))
      if (p.endsWith('/requests'))
        return new Promise<Response>((r) => {
          resolve = r
        })
      return Promise.resolve(json({ enabled: true, capacity: 100, count: 1 }))
    })
    await vi.advanceTimersByTimeAsync(5000)
    await fireEvent.click(screen.getByRole('button', { name: 'Clear history' }))
    await flushPromises()
    resolve(json({ requests: [entry()] }))
    await flushPromises()
    expect(screen.getByText('No requests recorded.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Inspect request 1' })).toBeNull()
  })

  it('localizes the new page in Italian', async () => {
    useLocale().setLocale('it')
    mount()
    expect(screen.getByRole('heading', { name: 'Diagnostica' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Esegui diagnostica' })).toBeTruthy()
    await fireEvent.click(screen.getByRole('button', { name: 'Richieste' }))
    await flushPromises()
    expect(screen.getByRole('button', { name: 'Esporta cronologia' })).toBeTruthy()
  })

  it('does not claim monitoring is disabled when its status cannot be loaded', async () => {
    fetchMock.mockRejectedValue(new TypeError('offline'))
    mount()
    await requests()
    expect(screen.getByRole('alert')).toBeTruthy()
    expect(screen.getByText('Monitoring status unavailable.')).toBeTruthy()
    expect(screen.queryByText(/Monitoring is disabled/)).toBeNull()
    expect((screen.getByRole('switch') as HTMLInputElement).disabled).toBe(true)
  })

  it('shows monitored stage failure even when the client received HTTP 200', async () => {
    const failed = entry()
    failed.stages.database!.status = 'failed'
    history = [failed]
    mount()
    await requests()
    expect(screen.getByText('The diagnostic pipeline did not complete.')).toBeTruthy()
  })

  it('keeps a failed database stage and subsequent not-run stages distinct', async () => {
    const r = report()
    r.stages.database!.status = 'failed'
    r.stages.processing!.status = 'not_run'
    r.stages.serialization!.status = 'not_run'
    fetchMock.mockResolvedValue(
      json({
        ...r,
        releases: [],
        errors: [{ stage: 'database', code: 'database_failed', message: 'Database search failed' }],
      }),
    )
    mount()
    await run()
    expect(screen.getByText('Failed')).toBeTruthy()
    expect(screen.getAllByText('Not run')).toHaveLength(2)
    expect(screen.getByText(/Database search failed/)).toBeTruthy()
    expect(screen.queryByText(/Violarr generated a validated/)).toBeNull()
  })

  it('bounds and projects the export contract at every level and redacts private strings', () => {
    const r = report()
    const payload = {
      ...r,
      original: {
        ...r.original,
        apikey: 'secret',
        q: 'private /Users/private magnet:?xt=secret token=secret https://private.test/secret',
      },
      processing: { ...r.processing, api_key: 'secret' },
      windows: [{ ...r.windows[0], sql: 'secret' }],
      releases: Array.from({ length: 2005 }, () => ({
        ...r.releases[0],
        title: 'private /Users/private',
        magnet: 'secret',
        score_rules: Array.from({ length: 105 }, (_, n) => n),
      })),
      errors: [
        {
          stage: 'database',
          code: 'database_failed',
          message: 'password=secret',
          traceback: 'secret',
        },
      ],
    }
    const exported = exportReport(payload as unknown as SearchReport)
    const parsed = JSON.parse(exported)
    expect(parsed.report_version).toBe(1)
    expect(parsed.application_version).toBe('1.2.3')
    expect(parsed.snapshot_version).toBe('snapshot-1')
    expect(parsed.counts.selected).toBe(1)
    expect(parsed.releases).toHaveLength(2000)
    expect(parsed.releases[0].score_rules).toHaveLength(100)
    expect(parsed.original.q).toContain('[redacted]')
    expect(exported).not.toContain('secret')
    expect(exported).not.toContain('/Users/private')
    expect(parsed.original.apikey).toBeUndefined()
    expect(parsed.processing.api_key).toBeUndefined()
    expect(parsed.windows[0].sql).toBeUndefined()
  })

  it('retains history on failed clear and keeps monitoring unchanged on failed toggle', async () => {
    mount()
    await requests()
    fetchMock.mockResolvedValue(json({ detail: 'Unavailable' }, 503))
    await fireEvent.click(screen.getByRole('switch'))
    await flushPromises()
    expect((screen.getByRole('switch') as HTMLInputElement).checked).toBe(false)
    expect(screen.getByRole('alert').textContent).toContain('maintenance')
    await fireEvent.click(screen.getByRole('button', { name: 'Clear history' }))
    await flushPromises()
    expect(screen.getByRole('button', { name: 'Inspect request 1' })).toBeTruthy()
  })

  it('does not overlap polling while a previous refresh is pending', async () => {
    vi.useFakeTimers()
    const view = mount()
    await requests()
    let resolve!: (value: Response) => void
    fetchMock.mockImplementation((p: string) =>
      p.endsWith('/requests')
        ? new Promise<Response>((r) => {
            resolve = r
          })
        : Promise.resolve(json({ enabled: false, capacity: 100, count: 1 })),
    )
    await vi.advanceTimersByTimeAsync(5000)
    const calls = fetchMock.mock.calls.length
    await vi.advanceTimersByTimeAsync(5000)
    expect(fetchMock.mock.calls.length).toBe(calls)
    view.unmount()
    resolve(json({ requests: [] }))
    await flushPromises()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('shows an explicit error when a search returns no report', async () => {
    fetchMock.mockResolvedValue(json(null))
    mount()
    await run()
    expect(screen.getByRole('alert').textContent).toContain('No diagnostic report was returned')
    expect(
      (screen.getByRole('button', { name: 'Export report' }) as HTMLButtonElement).disabled,
    ).toBe(true)
  })

  it('aborts the sibling history request when monitoring refresh fails', async () => {
    let signal: AbortSignal | undefined
    let resolve!: (value: Response) => void
    fetchMock.mockImplementation((p: string, init?: RequestInit) => {
      if (p.endsWith('/monitoring')) return Promise.reject(new TypeError('offline'))
      signal = init?.signal as AbortSignal
      return new Promise<Response>((r) => {
        resolve = r
      })
    })
    const view = mount()
    await requests()
    expect(signal?.aborted).toBe(true)
    view.unmount()
    resolve(json({ requests: [] }))
    await flushPromises()
  })
})
