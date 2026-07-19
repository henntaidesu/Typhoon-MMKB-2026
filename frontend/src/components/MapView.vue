<template>
  <div class="map-wrap">
    <div ref="mapEl" class="map"></div>

    <!-- Satellite cloud imagery toggle (NASA GIBS, synced to the timeline date) -->
    <div class="clouds-card">
      <label class="clouds-toggle">
        <input type="checkbox" v-model="showClouds" />
        <span>{{ t('map.clouds') }}</span>
      </label>
      <div v-if="showClouds" class="clouds-body">
        <select class="clouds-src" v-model="cloudSource">
          <option value="viirs">{{ t('map.cloudSourceViirs') }}</option>
          <option value="terra">{{ t('map.cloudSourceTerra') }}</option>
          <option value="aqua">{{ t('map.cloudSourceAqua') }}</option>
        </select>
        <div class="clouds-date">
          {{ t('map.cloudsDateHint') }}: {{ cloudDate || t('map.cloudsLatest') }}
        </div>
        <label class="clouds-opacity">
          {{ t('map.cloudsOpacity') }}
          <input type="range" min="0.1" max="1" step="0.05" v-model.number="cloudOpacity" />
        </label>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import L from 'leaflet'
import { useTyphoonStore } from '../stores/typhoon'

const { t } = useI18n()
const store = useTyphoonStore()
const mapEl = ref(null)
let map = null
let trackLayer = null
let regionLayer = null
let disasterLayer = null
let landfallLayer = null
let searchLayer = null // located hits of the current search
let cursor = null // moving position marker

// --- Satellite cloud imagery (NASA GIBS, no API key) ---
const showClouds = ref(false)
// VIIRS by default: its wide swath overlaps between orbits, so the daily
// mosaic has no wedge-shaped gaps (MODIS's narrower swath tears at low lat).
const cloudSource = ref('viirs')
const cloudOpacity = ref(0.75)
let cloudLayer = null

// GIBS layer ids (EPSG:3857 / Web Mercator), all daily true-color with a
// multi-year archive so they resolve for historical typhoons.
const GIBS = {
  terra: { id: 'MODIS_Terra_CorrectedReflectance_TrueColor', level: 9 },
  aqua: { id: 'MODIS_Aqua_CorrectedReflectance_TrueColor', level: 9 },
  viirs: { id: 'VIIRS_SNPP_CorrectedReflectance_TrueColor', level: 9 },
}

// Date (YYYY-MM-DD) of the point the timeline cursor is on; drives the imagery
// day. Null when no typhoon is selected → GIBS serves the latest ('default').
const cloudDate = computed(() => {
  const pts = store.track?.properties?.points
  const time = pts?.[store.timeIndex]?.time || pts?.[pts.length - 1]?.time
  return time ? String(time).slice(0, 10) : null
})

function cloudUrl() {
  const cfg = GIBS[cloudSource.value] || GIBS.terra
  const time = cloudDate.value || 'default'
  return `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/${cfg.id}` +
    `/default/${time}/GoogleMapsCompatible_Level${cfg.level}/{z}/{y}/{x}.jpg`
}

function renderClouds() {
  cloudLayer = clear(cloudLayer)
  if (!showClouds.value || !map) return
  const cfg = GIBS[cloudSource.value] || GIBS.terra
  cloudLayer = L.tileLayer(cloudUrl(), {
    attribution: 'Imagery &copy; NASA EOSDIS GIBS',
    maxNativeZoom: cfg.level, maxZoom: 12, opacity: cloudOpacity.value,
    // Sit above the OSM base tiles but keep vector overlays on top.
    zIndex: 250,
  }).addTo(map)
}

// Saffir-Simpson-ish color ramp by max sustained wind (kt).
function windColor(kt) {
  if (kt == null) return '#8aa0b6'
  if (kt >= 137) return '#7b241c' // Cat5
  if (kt >= 113) return '#c0392b' // Cat4
  if (kt >= 96) return '#e67e22'  // Cat3
  if (kt >= 83) return '#f1c40f'  // Cat2
  if (kt >= 64) return '#f39c12'  // Cat1
  if (kt >= 34) return '#2ecc71'  // TS
  return '#3498db'                // TD
}

// Publish the viewport as "minLon,minLat,maxLon,maxLat" so a search can be
// restricted to what the user is currently looking at (spatio-temporal join).
function publishBbox() {
  const b = map.getBounds()
  store.setBbox([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
    .map((v) => v.toFixed(4)).join(','))
}

onMounted(() => {
  map = L.map(mapEl.value, { worldCopyJump: true }).setView([20, 135], 4)
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap', maxZoom: 12,
  }).addTo(map)
  map.on('moveend', publishBbox)
  publishBbox()
})

function clear(layer) { if (layer) { map.removeLayer(layer); } return null }

// Toggle / switch source → rebuild the imagery layer.
watch([showClouds, cloudSource], renderClouds)
// Scrubbing the timeline (or selecting a typhoon) changes the imagery day.
watch(cloudDate, () => { if (showClouds.value && cloudLayer) cloudLayer.setUrl(cloudUrl()) })
// Opacity slider → no rebuild, just restyle.
watch(cloudOpacity, (v) => { if (cloudLayer) cloudLayer.setOpacity(v) })

watch(() => store.track, (track) => {
  trackLayer = clear(trackLayer)
  cursor = clear(cursor)
  if (!track?.geometry?.coordinates?.length) return
  const coords = track.geometry.coordinates
  const pts = track.properties.points
  const group = L.layerGroup()
  // Draw each segment colored by the wind at its start point.
  for (let i = 0; i < coords.length - 1; i++) {
    const a = [coords[i][1], coords[i][0]]
    const b = [coords[i + 1][1], coords[i + 1][0]]
    L.polyline([a, b], { color: windColor(pts[i]?.wind_kt), weight: 4, opacity: 0.9 }).addTo(group)
  }
  // Observation dots.
  coords.forEach((c, i) => {
    L.circleMarker([c[1], c[0]], {
      radius: 3, color: windColor(pts[i]?.wind_kt), fillOpacity: 1,
    }).bindTooltip(
      `${pts[i]?.time?.slice(0, 16) || ''}<br>wind ${pts[i]?.wind_kt ?? '?'} kt · ${pts[i]?.pressure_hpa ?? '?'} hPa`,
    ).addTo(group)
  })
  group.addTo(map)
  trackLayer = group
  map.fitBounds(L.latLngBounds(coords.map((c) => [c[1], c[0]])).pad(0.2))
})

watch(() => store.regions, (regions) => {
  regionLayer = clear(regionLayer)
  if (!regions?.features?.length) return
  regionLayer = L.geoJSON(regions, {
    style: { color: '#0b6bcb', weight: 1, fillColor: '#0b6bcb', fillOpacity: 0.08, dashArray: '4' },
  }).addTo(map)
})

watch(() => store.disasters, (disasters) => {
  disasterLayer = clear(disasterLayer)
  if (!disasters?.features?.length) return
  disasterLayer = L.geoJSON(disasters, {
    pointToLayer: (f, latlng) => L.marker(latlng),
    onEachFeature: (f, layer) => {
      const p = f.properties
      layer.bindPopup(
        `<b>${p.disaster_type}</b><br>${(p.description || '').slice(0, 200)}<br>` +
        (p.casualties ? `${t('map.casualties')}: ${p.casualties}<br>` : '') +
        (p.source ? `${t('map.source')}: ${p.source}` : '') +
        (p.source_url ? ` · <a href="${p.source_url}" target="_blank">${t('map.link')}</a>` : ''),
      )
    },
  }).addTo(map)
})

watch(() => store.landfalls, (landfalls) => {
  landfallLayer = clear(landfallLayer)
  if (!landfalls?.features?.length) return
  landfallLayer = L.geoJSON(landfalls, {
    pointToLayer: (f, latlng) => L.circleMarker(latlng, {
      radius: 7, color: '#7b241c', weight: 2, fillColor: '#e0392b', fillOpacity: 0.95,
    }),
    onEachFeature: (f, layer) => {
      const p = f.properties
      layer.bindTooltip(
        `<b>${t('detail.landfall')}</b> ${p.country || ''}<br>` +
        `${(p.landfall_time || '').slice(0, 16)}<br>` +
        `wind ${p.wind_kt ?? '?'} kt · ${p.pressure_hpa ?? '?'} hPa`,
      )
    },
  }).addTo(map)
})

// The whole located result set, drawn as one layer so a search reads
// geographically at a glance instead of one hit at a time. Kept visually
// distinct from the selected typhoon's own disaster markers.
watch(() => store.searchPins, (pins) => {
  searchLayer = clear(searchLayer)
  if (!pins?.length || !map) return
  const group = L.layerGroup()
  for (const h of pins) {
    L.circleMarker([h.lat, h.lon], {
      radius: 6, weight: 2, color: '#fff',
      fillColor: h.kind === 'disaster' ? '#a5342a' : '#24558f', fillOpacity: 0.9,
    })
      .bindTooltip(`<b>${h.disaster_type || h.info_type}</b><br>` +
        `${(h.title || h.description || '').slice(0, 120)}`)
      .on('click', () => store.openHit(h))
      .addTo(group)
  }
  group.addTo(map)
  searchLayer = group
  // Frame the hits only when nothing is selected — once the user opens a
  // typhoon, its track owns the viewport.
  if (!store.selectedId) {
    map.fitBounds(L.latLngBounds(pins.map((h) => [h.lat, h.lon])).pad(0.3))
  }
})

// Clicking a located search hit flies the map to it. Runs after the track's own
// fitBounds (which the same click triggers), so it wins and the hit stays framed.
watch(() => store.focus, (f) => {
  if (!f || !map) return
  setTimeout(() => map.flyTo([f.lat, f.lon], Math.max(map.getZoom(), 6)), 300)
})

// Timeline playback: show a pulsing cursor at the current track index.
watch(() => store.timeIndex, (i) => {
  const pts = store.track?.geometry?.coordinates
  if (!pts || !pts[i]) return
  const latlng = [pts[i][1], pts[i][0]]
  const w = store.track.properties.points[i]?.wind_kt
  if (!cursor) {
    cursor = L.circleMarker(latlng, { radius: 9, color: '#111', weight: 2, fillColor: windColor(w), fillOpacity: 0.95 }).addTo(map)
  } else {
    cursor.setLatLng(latlng).setStyle({ fillColor: windColor(w) })
  }
})
</script>

<style scoped>
.map-wrap { position: absolute; inset: 0; }
.map { position: absolute; inset: 0; }

/* Cloud-imagery control, stacked below the intensity legend (top-right). */
.clouds-card {
  position: absolute; right: 12px; top: 132px; z-index: 500;
  background: rgba(255, 255, 255, .94); border-radius: 8px;
  padding: 8px 10px; font-size: 12px; box-shadow: 0 1px 6px rgba(0, 0, 0, .12);
  width: 168px;
}
.clouds-toggle { display: flex; align-items: center; gap: 6px; cursor: pointer; font-weight: 600; }
.clouds-toggle input { cursor: pointer; }
.clouds-body { margin-top: 8px; display: flex; flex-direction: column; gap: 6px; }
.clouds-src { width: 100%; font-size: 12px; padding: 3px 4px; border: 1px solid #cfd6df; border-radius: 5px; }
.clouds-date { color: #566; font-size: 11px; }
.clouds-opacity { display: flex; flex-direction: column; gap: 2px; font-size: 11px; color: #566; }
.clouds-opacity input { width: 100%; }
</style>
