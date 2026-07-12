<template>
  <div ref="mapEl" class="map"></div>
</template>

<script setup>
import { onMounted, ref, watch } from 'vue'
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
let cursor = null // moving position marker

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

onMounted(() => {
  map = L.map(mapEl.value, { worldCopyJump: true }).setView([20, 135], 4)
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap', maxZoom: 12,
  }).addTo(map)
})

function clear(layer) { if (layer) { map.removeLayer(layer); } return null }

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
.map { position: absolute; inset: 0; }
</style>
