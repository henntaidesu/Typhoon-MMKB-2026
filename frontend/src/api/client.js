// Thin API wrapper. In dev, '/api' is proxied to http://localhost:8000 (see vite.config.js).
const BASE = import.meta.env.VITE_API_BASE || '/api'

async function get(path, params) {
  const url = new URL(BASE + path, window.location.origin)
  if (params) Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v)
  })
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${path} -> ${r.status}`)
  return r.json()
}

async function post(path, body) {
  const r = await fetch(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) {
    // Surface FastAPI's {detail: "..."} message when present.
    const msg = await r.json().then((j) => j.detail).catch(() => null)
    throw new Error(msg || `${path} -> ${r.status}`)
  }
  return r.json()
}

export default {
  listTyphoons: (params) => get('/typhoons', params),
  getTyphoon: (id) => get(`/typhoons/${id}`),
  getTrack: (id) => get(`/typhoons/${id}/track`),
  getDisasters: (id) => get(`/typhoons/${id}/disasters`),
  getRegions: (id) => get(`/typhoons/${id}/affected-regions`),
  // body: { q, k, bbox?, date_from?, date_to?, max_distance? }
  semantic: (body) => post('/search/semantic', { k: 10, ...body }),
  spatiotemporal: (params) => get('/search/spatiotemporal', params),
  hybrid: (params) => get('/search/hybrid', params),
  stats: () => get('/search/stats'),
  // Geographic impact analytics (统计 page)
  statsByCountry: () => get('/stats/by-country'),
  statsByRegion: (params) => get('/stats/by-region', params),
  landfallGeojson: (params) => get('/stats/landfall-geojson', params),
  regionTracks: (id, params) => get(`/stats/region/${id}/tracks`, params),
  typhoonCountries: (id) => get(`/typhoons/${id}/countries`),
  typhoonLandfalls: (id) => get(`/typhoons/${id}/landfalls`),
  // Data sources (数据源 page)
  listSources: () => get('/sources'),
  sourcesStatus: () => get('/sources/status'),
  startCrawl: (key, body) => post(`/sources/${key}/crawl`, body),
}
