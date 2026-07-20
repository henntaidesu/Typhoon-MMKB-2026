import { defineStore } from 'pinia'
import api from '../api/client'

// Request sequencing. Every action here fans out to the network and then writes
// shared state, so without a token the slower of two overlapping calls wins:
// click typhoon A then B and A's six requests can land last, leaving the panel
// headed "B" while the track, 受灾情报 and 公共情报 below it are all A's.
//
// Two counters because two independent groups of state are involved: the
// selection (track / disasters / regions / …) and the result list (list /
// search / loading). loadList and semanticSearch share the second one — they
// write the same fields, so they must be ordered against each other too.
let selectionSeq = 0
let listSeq = 0

export const useTyphoonStore = defineStore('typhoon', {
  state: () => ({
    list: [],            // typhoon briefs (list or search results)
    filters: { year: null, name: '', min_wind: null },
    selectedId: null,
    track: null,         // GeoJSON Feature (LineString)
    disasters: null,     // 受灾情报 GeoJSON FeatureCollection
    publicInfos: null,   // 公共情报 FeatureCollection + `unlocated` (records with no geometry)
    regions: null,       // GeoJSON FeatureCollection
    countries: [],       // affected admin regions [{name, iso_a3, landfall, ...}]
    landfalls: null,     // GeoJSON FeatureCollection (landfall points)
    // Full result of the last search: all three knowledge layers, not just the
    // typhoon one. Null when no search is active (sidebar shows the plain list).
    search: null,        // { query, intent, scoped, structured, typhoons, disasters, public_info }
    // Spatio-temporal scope for the next search — the map's current viewport and
    // an optional year range. Turns the search into a 時空間 x 意味 join.
    scope: { useBbox: false, bbox: null, yearFrom: null, yearTo: null },
    focus: null,         // { lat, lon } a result the map should fly to
    timeIndex: 0,        // current index along the track for playback
    loading: false,
    error: null,
  }),
  getters: {
    selected: (s) => s.list.find((t) => t.id === s.selectedId) || null,
    trackPoints: (s) => s.track?.properties?.points || [],
    resultCount: (s) => s.search
      ? s.search.structured.length + s.search.typhoons.length +
        s.search.disasters.length + s.search.public_info.length
      : 0,
    // Located search hits, for plotting the whole result set on the map at once
    // — otherwise a hit's position is only visible after clicking it.
    searchPins: (s) => {
      if (!s.search) return []
      return [
        ...s.search.disasters.map((d) => ({ ...d, kind: 'disaster' })),
        ...s.search.public_info.map((p) => ({ ...p, kind: 'public' })),
      ].filter((h) => h.lat != null && h.lon != null)
    },
  },
  actions: {
    async loadList() {
      const seq = ++listSeq
      this.loading = true; this.error = null
      try {
        const rows = await api.listTyphoons({
          year: this.filters.year,
          name: this.filters.name,
          min_wind: this.filters.min_wind,
        })
        if (seq !== listSeq) return  // a newer list/search request has taken over
        this.list = rows
        this.search = null
      } catch (e) {
        if (seq === listSeq) this.error = String(e)
      } finally {
        if (seq === listSeq) this.loading = false
      }
    },
    async select(id) {
      const seq = ++selectionSeq
      this.selectedId = id
      this.timeIndex = 0
      this.track = this.disasters = this.regions = this.landfalls = null
      this.publicInfos = null
      this.countries = []
      try {
        const [track, disasters, publicInfos, regions, countries, landfalls] = await Promise.all([
          api.getTrack(id).catch(() => null),
          api.getDisasters(id).catch(() => null),
          api.getPublicInfo(id).catch(() => null),
          api.getRegions(id).catch(() => null),
          api.typhoonCountries(id).catch(() => []),
          api.typhoonLandfalls(id).catch(() => null),
        ])
        // Another storm was clicked while these were in flight; writing now
        // would pair this storm's data with that storm's header.
        if (seq !== selectionSeq) return
        this.track = track; this.disasters = disasters; this.regions = regions
        this.publicInfos = publicInfos
        this.countries = countries || []; this.landfalls = landfalls
        this.timeIndex = (track?.properties?.points?.length || 1) - 1
      } catch (e) {
        if (seq === selectionSeq) this.error = String(e)
      }
    },
    async semanticSearch(q) {
      const seq = ++listSeq
      this.loading = true; this.error = null
      try {
        const { useBbox, bbox, yearFrom, yearTo } = this.scope
        const res = await api.semantic({
          q, k: 15,
          bbox: useBbox && bbox ? bbox : undefined,
          date_from: yearFrom ? `${yearFrom}-01-01T00:00:00` : undefined,
          date_to: yearTo ? `${yearTo}-12-31T23:59:59` : undefined,
        })
        if (seq !== listSeq) return  // superseded by a later search or a clear
        this.search = res
        // The typhoon list keeps showing storms so the map/detail panel still
        // work; the disaster & public-info hits get their own result sections.
        this.list = [...res.structured, ...res.typhoons]
      } catch (e) {
        if (seq === listSeq) { this.error = String(e); this.search = null }
      } finally {
        if (seq === listSeq) this.loading = false
      }
    },
    clearSearch() {
      // loadList bumps the sequence, which also cancels any search still in
      // flight — otherwise a slow search lands afterwards and un-clears itself.
      this.search = null
      return this.loadList()
    },
    setBbox(bbox) { this.scope.bbox = bbox },
    // Select the hit's typhoon and ask the map to fly to the hit's own location.
    // A disaster/public-info hit can belong to a storm that isn't in `list`
    // (its own vector matched, the storm's didn't), so pull the brief in first —
    // otherwise the `selected` getter finds nothing and the detail panel stays shut.
    async openHit(hit) {
      const id = hit.typhoon_id
      if (id == null) return
      if (!this.list.some((t) => t.id === id)) {
        try { this.list = [...this.list, await api.getTyphoon(id)] } catch { /* keep going */ }
      }
      await this.select(id)
      if (hit.lat != null && hit.lon != null) this.focus = { lat: hit.lat, lon: hit.lon }
    },
    setTimeIndex(i) { this.timeIndex = i },
  },
})
