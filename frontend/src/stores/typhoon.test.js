/**
 * Tests for the typhoon store's request sequencing.
 *
 * Every action here fans out to the network and then writes shared state, so
 * overlapping calls race: whichever response lands last wins, regardless of
 * which the user actually asked for. The failure is silent and reads as bad
 * data rather than a bug — a detail panel headed "B" showing A's track and A's
 * 受灾情报, which is indistinguishable from a mis-attributed record.
 *
 * Timing is the whole subject, so the fake API resolves on explicit per-endpoint
 * delays and each test interleaves calls deliberately.
 */
import { describe, test, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// Per-endpoint latency, set by each test to force a specific interleaving.
const delays = { track: 0, semantic: 0, list: 0 }
const brief = (id) => ({ id, intl_id: String(id), name: `T${id}`, season_year: 2020 })

vi.mock('../api/client', () => ({
  default: {
    listTyphoons: async () => { await sleep(delays.list); return [brief(1), brief(2)] },
    getTyphoon: async (id) => brief(id),
    getTrack: async (id) => {
      await sleep(delays.track)
      return { properties: { points: [{ owner: id }] }, geometry: { coordinates: [[0, 0]] } }
    },
    getDisasters: async (id) => ({ features: [{ properties: { id, owner: id } }] }),
    getPublicInfo: async (id) => ({ features: [], unlocated: [{ id, owner: id }] }),
    getRegions: async () => null,
    typhoonCountries: async (id) => [{ admin_region_id: id, name: `C${id}` }],
    typhoonLandfalls: async () => null,
    semantic: async (body) => {
      await sleep(delays.semantic)
      return {
        query: body.q, intent: 'semantic', scoped: false, max_distance: 0.6,
        structured: [], typhoons: [{ ...brief(9), distance: 0.1 }],
        disasters: [], public_info: [],
      }
    },
  },
}))

const { useTyphoonStore } = await import('./typhoon')

beforeEach(() => {
  setActivePinia(createPinia())
  delays.track = delays.semantic = delays.list = 0
})

describe('select()', () => {
  test('a slow earlier selection cannot overwrite a later one', async () => {
    const store = useTyphoonStore()

    delays.track = 50               // storm 1 answers slowly
    const first = store.select(1)
    await sleep(5)
    delays.track = 0                // storm 2 answers at once
    const second = store.select(2)

    await Promise.all([first, second])

    expect(store.selectedId).toBe(2)
    // Every panel section must describe the storm named in the header.
    expect(store.track.properties.points[0].owner).toBe(2)
    expect(store.disasters.features[0].properties.owner).toBe(2)
    expect(store.publicInfos.unlocated[0].owner).toBe(2)
    expect(store.countries[0].admin_region_id).toBe(2)
  })

  test('a single selection still loads normally', async () => {
    const store = useTyphoonStore()
    await store.select(1)
    expect(store.track.properties.points[0].owner).toBe(1)
    expect(store.disasters.features[0].properties.owner).toBe(1)
  })
})

describe('search', () => {
  test('a slow earlier search cannot overwrite a later one', async () => {
    const store = useTyphoonStore()

    delays.semantic = 50
    const first = store.semanticSearch('slow query')
    await sleep(5)
    delays.semantic = 0
    const second = store.semanticSearch('fast query')

    await Promise.all([first, second])
    expect(store.search.query).toBe('fast query')
    expect(store.loading).toBe(false)
  })

  test('clearing beats a search still in flight', async () => {
    const store = useTyphoonStore()

    delays.semantic = 50
    const searching = store.semanticSearch('q')
    await sleep(5)
    await store.clearSearch()
    await searching

    // A slow search landing after the clear would silently un-clear the sidebar.
    expect(store.search).toBeNull()
  })

  test('a stale list response does not wipe a newer search', async () => {
    const store = useTyphoonStore()

    delays.list = 60
    const listing = store.loadList()            // slow, started first
    await sleep(5)
    delays.semantic = 10
    const searching = store.semanticSearch('q') // newer, finishes first

    await Promise.all([listing, searching])
    expect(store.search).not.toBeNull()
    expect(store.search.query).toBe('q')
    expect(store.loading).toBe(false)
  })
})
