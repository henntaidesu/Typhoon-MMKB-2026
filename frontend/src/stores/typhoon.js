import { defineStore } from 'pinia'
import api from '../api/client'

export const useTyphoonStore = defineStore('typhoon', {
  state: () => ({
    list: [],            // typhoon briefs (list or search results)
    filters: { year: null, name: '', min_wind: null },
    selectedId: null,
    track: null,         // GeoJSON Feature (LineString)
    disasters: null,     // GeoJSON FeatureCollection
    regions: null,       // GeoJSON FeatureCollection
    countries: [],       // affected admin regions [{name, iso_a3, landfall, ...}]
    landfalls: null,     // GeoJSON FeatureCollection (landfall points)
    semanticHits: new Set(), // ids highlighted by semantic search
    timeIndex: 0,        // current index along the track for playback
    loading: false,
    error: null,
  }),
  getters: {
    selected: (s) => s.list.find((t) => t.id === s.selectedId) || null,
    trackPoints: (s) => s.track?.properties?.points || [],
  },
  actions: {
    async loadList() {
      this.loading = true; this.error = null
      try {
        this.list = await api.listTyphoons({
          year: this.filters.year,
          name: this.filters.name,
          min_wind: this.filters.min_wind,
        })
        this.semanticHits = new Set()
      } catch (e) { this.error = String(e) } finally { this.loading = false }
    },
    async select(id) {
      this.selectedId = id
      this.timeIndex = 0
      this.track = this.disasters = this.regions = this.landfalls = null
      this.countries = []
      try {
        const [track, disasters, regions, countries, landfalls] = await Promise.all([
          api.getTrack(id).catch(() => null),
          api.getDisasters(id).catch(() => null),
          api.getRegions(id).catch(() => null),
          api.typhoonCountries(id).catch(() => []),
          api.typhoonLandfalls(id).catch(() => null),
        ])
        this.track = track; this.disasters = disasters; this.regions = regions
        this.countries = countries || []; this.landfalls = landfalls
        this.timeIndex = (track?.properties?.points?.length || 1) - 1
      } catch (e) { this.error = String(e) }
    },
    async semanticSearch(q) {
      this.loading = true; this.error = null
      try {
        const res = await api.semantic(q, 15)
        this.list = res.typhoons
        this.semanticHits = new Set(res.typhoons.map((t) => t.id))
      } catch (e) { this.error = String(e) } finally { this.loading = false }
    },
    setTimeIndex(i) { this.timeIndex = i },
  },
})
