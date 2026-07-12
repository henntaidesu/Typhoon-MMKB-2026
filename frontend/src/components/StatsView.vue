<template>
  <div class="stats">
    <div v-if="err" class="err">{{ t('stats.loadFailed') }}{{ err }}</div>

    <!-- 汇总磁贴 -->
    <div class="tiles">
      <div class="tile"><div class="num">{{ summary.total_typhoons ?? '—' }}</div><div class="cap">{{ t('stats.totalTyphoons') }}</div></div>
      <div class="tile"><div class="num">{{ summary.total_landfalls ?? '—' }}</div><div class="cap">{{ t('stats.totalLandfalls') }}</div></div>
      <div class="tile"><div class="num">{{ summary.total_disasters ?? '—' }}</div><div class="cap">{{ t('stats.totalDisasters') }}</div></div>
    </div>

    <div class="cols">
      <!-- 左：登陆频次分级地图 + 交互轨迹 -->
      <section class="card map-card">
        <div class="card-head">
          <h3>{{ t('stats.regionFrequency') }}</h3>
          <div class="level-toggle">
            <button :class="{ on: level === 0 }" @click="setLevel(0)">{{ t('stats.byCountry') }}</button>
            <button :class="{ on: level === 1 }" @click="setLevel(1)">{{ t('stats.byProvince') }}</button>
            <button :class="{ on: level === 2 }" @click="setLevel(2)">{{ t('stats.byCity') }}</button>
          </div>
        </div>

        <div class="map-wrap">
          <div ref="mapEl" class="choropleth"></div>

          <!-- 选中区域后浮出的相关台风面板 -->
          <div v-if="selected" class="track-panel">
            <div class="tp-head">
              <div class="tp-title">
                {{ selected.region_name }}
                <span v-if="selected.parent_name" class="tp-parent">· {{ selected.parent_name }}</span>
              </div>
              <button class="tp-x" @click="clearSelection">×</button>
            </div>
            <div class="tp-sub">
              {{ t('stats.relatedTyphoons') }}:
              <b>{{ selected.typhoon_count }}</b>
              <span class="tp-showing">{{ t('stats.showingOf', { shown: features.length, total: selected.typhoon_count }) }}</span>
            </div>
            <ul class="tp-list">
              <li v-for="f in features" :key="f.properties.typhoon_id"
                  :class="{ on: hoverId === f.properties.typhoon_id }"
                  @mouseenter="highlight(f.properties.typhoon_id)"
                  @mouseleave="highlight(null)">
                <span class="dot" :style="{ background: windColor(f.properties.max_wind_kt) }"></span>
                <span class="nm">{{ f.properties.name || f.properties.intl_id }}</span>
                <span class="yr">{{ f.properties.season_year }}</span>
                <span class="wd">{{ f.properties.max_wind_kt ?? '?' }}kt</span>
              </li>
            </ul>
          </div>
        </div>

        <div class="ramp" v-show="!selected">
          <span>{{ t('stats.rampLow') }}</span>
          <i v-for="c in rampColors" :key="c" :style="{ background: c }"></i>
          <span>{{ t('stats.rampHigh') }} ({{ t('stats.maxTimes', { n: maxCount }) }})</span>
        </div>
        <div class="hint" v-show="!selected">{{ t('stats.clickHint') }}</div>
      </section>

      <!-- 右：柱状统计（可点击） -->
      <section class="card">
        <h3>{{ t('stats.topCountries') }}</h3>
        <BarChart :items="countryBars" color="#c0392b" :active-id="activeRegionId" @select="onBar" />
        <h3 style="margin-top:18px">{{ t('stats.topRegions') }}</h3>
        <BarChart :items="regionBars" color="#0b6bcb" :active-id="activeRegionId" @select="onBar" />
        <h3 style="margin-top:18px">{{ t('stats.typhoonsByYear') }}</h3>
        <BarChart :items="yearBars" color="#2e9e5b" />
        <h3 style="margin-top:18px">{{ t('stats.disasterTypes') }}</h3>
        <BarChart :items="typeBars" color="#e67e22" />
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import L from 'leaflet'
import api from '../api/client'
import BarChart from './BarChart.vue'

const { t } = useI18n()
const err = ref(null)
const summary = reactive({})
const countryBars = ref([])
const regionBars = ref([])
const yearBars = ref([])
const typeBars = ref([])
const level = ref(0)
const maxCount = ref(0)

const selected = ref(null)   // { region_id, region_name, parent_name, typhoon_count }
const features = ref([])     // related typhoon track features
const hoverId = ref(null)
const activeRegionId = ref(null)

const mapEl = ref(null)
let map = null
let choroLayer = null
let tracksLayer = null

// Sequential blue ramp for the choropleth (landfall frequency).
const rampColors = ['#eaf3fc', '#c6dbf0', '#8ebde0', '#4f95cf', '#1f6fb8', '#0b4a86']
function rampColor(count, max) {
  if (!count) return '#f3f5f8'
  const tt = Math.min(1, count / Math.max(1, max))
  return rampColors[Math.min(rampColors.length - 1, Math.floor(tt * rampColors.length))]
}
// Saffir-Simpson-ish ramp for track intensity (same as MapView).
function windColor(kt) {
  if (kt == null) return '#8aa0b6'
  if (kt >= 137) return '#7b241c'
  if (kt >= 113) return '#c0392b'
  if (kt >= 96) return '#e67e22'
  if (kt >= 83) return '#f1c40f'
  if (kt >= 64) return '#f39c12'
  if (kt >= 34) return '#2ecc71'
  return '#3498db'
}

async function loadSummary() {
  const s = await api.stats()
  Object.assign(summary, s)
  yearBars.value = (s.typhoons_by_year || []).map((r) => ({ label: String(r.year), value: r.count }))
  typeBars.value = (s.disasters_by_type || []).map((r) => ({ label: r.type, value: r.count }))
}

async function loadCountryBars() {
  const rows = await api.statsByCountry()
  countryBars.value = rows.slice(0, 12).map((r) => ({
    label: r.country, value: r.typhoon_count, id: r.admin_region_id,
  }))
}

async function loadRegionBars() {
  const rows = await api.statsByRegion({ level: level.value === 0 ? 1 : level.value })
  regionBars.value = rows.slice(0, 12).map((r) => ({
    label: r.name + (r.parent_name ? ` (${r.parent_name})` : r.country && level.value !== 0 ? ` (${r.country})` : ''),
    value: r.landfall_count, id: r.admin_region_id,
  }))
}

async function loadChoropleth() {
  if (!map) return
  const fc = await api.landfallGeojson({ level: level.value })
  maxCount.value = fc.properties?.max_landfall_count || 0
  if (choroLayer) { map.removeLayer(choroLayer); choroLayer = null }
  choroLayer = L.geoJSON(fc, {
    style: (f) => choroStyle(f),
    onEachFeature: (f, layer) => {
      const p = f.properties
      layer.bindTooltip(
        `<b>${p.name || '—'}</b>${p.country ? ' · ' + p.country : ''}<br/>` +
        t('stats.tooltipLandfall', { lf: p.landfall_count, imp: p.impact_count }),
        { sticky: true },
      )
      layer.on('click', () => selectRegion(p.id, p.name))
    },
  }).addTo(map)
}

function choroStyle(f) {
  const on = activeRegionId.value === f.properties.id
  return {
    fillColor: rampColor(f.properties.landfall_count, maxCount.value),
    fillOpacity: selected.value ? 0.35 : 0.75,
    color: on ? '#0b2540' : '#5b6b7f', weight: on ? 2.5 : 0.6,
  }
}

async function selectRegion(regionId, name) {
  if (regionId == null) return
  try {
    const fc = await api.regionTracks(regionId, {})
    features.value = fc.features
    selected.value = { ...fc.properties, region_name: fc.properties.region_name || name }
    activeRegionId.value = regionId
    drawTracks()
    if (choroLayer) choroLayer.setStyle(choroStyle)
  } catch (e) { err.value = String(e.message || e) }
}

function drawTracks() {
  if (tracksLayer) { map.removeLayer(tracksLayer); tracksLayer = null }
  if (!features.value.length) return
  tracksLayer = L.geoJSON({ type: 'FeatureCollection', features: features.value }, {
    style: (f) => ({ color: windColor(f.properties.max_wind_kt), weight: 2, opacity: 0.55 }),
    onEachFeature: (f, layer) => {
      layer._tid = f.properties.typhoon_id
      layer.bindTooltip(
        `${f.properties.name || f.properties.intl_id} · ${f.properties.season_year} · ${f.properties.max_wind_kt ?? '?'}kt`,
      )
      layer.on('mouseover', () => highlight(f.properties.typhoon_id))
      layer.on('mouseout', () => highlight(null))
    },
  }).addTo(map)
  try { map.fitBounds(tracksLayer.getBounds().pad(0.15)) } catch { /* empty */ }
}

function highlight(tid) {
  hoverId.value = tid
  if (!tracksLayer) return
  tracksLayer.eachLayer((l) => {
    const on = l._tid === tid
    l.setStyle({ weight: on ? 4 : 2, opacity: on ? 1 : (tid ? 0.25 : 0.55) })
    if (on) l.bringToFront()
  })
}

function clearSelection() {
  selected.value = null
  features.value = []
  activeRegionId.value = null
  if (tracksLayer) { map.removeLayer(tracksLayer); tracksLayer = null }
  if (choroLayer) choroLayer.setStyle(choroStyle)
}

function onBar(item) { selectRegion(item.id, item.label) }

function setLevel(l) {
  if (level.value === l) return
  level.value = l
  clearSelection()
}

watch(level, async () => {
  try { await Promise.all([loadRegionBars(), loadChoropleth()]) }
  catch (e) { err.value = String(e.message || e) }
})

onMounted(async () => {
  await nextTick()
  map = L.map(mapEl.value, { worldCopyJump: true }).setView([22, 128], 3)
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap', maxZoom: 10,
  }).addTo(map)
  try {
    await Promise.all([loadSummary(), loadCountryBars(), loadRegionBars(), loadChoropleth()])
  } catch (e) { err.value = String(e.message || e) }
})
onUnmounted(() => { if (map) { map.remove(); map = null } })
</script>

<style scoped>
.stats { height: 100%; overflow-y: auto; padding: 20px 24px; background: #eef1f5; }
.err { background: #fdecec; color: #b93b3b; padding: 10px 12px; border-radius: 8px; margin-bottom: 14px; font-size: 13px; }

.tiles { display: flex; gap: 14px; margin-bottom: 16px; }
.tile { flex: 1; background: #fff; border: 1px solid #e3e8ef; border-radius: 12px; padding: 16px 18px; box-shadow: 0 1px 3px rgba(16,34,60,.05); }
.tile .num { font-size: 30px; font-weight: 700; color: #0b2540; }
.tile .cap { font-size: 13px; color: #6b7787; margin-top: 2px; }

.cols { display: grid; grid-template-columns: 1.15fr 1fr; gap: 16px; align-items: start; }
@media (max-width: 900px) { .cols { grid-template-columns: 1fr; } }

.card { background: #fff; border: 1px solid #e3e8ef; border-radius: 12px; padding: 16px; box-shadow: 0 1px 3px rgba(16,34,60,.05); }
.card h3 { margin: 0 0 10px; font-size: 15px; color: #1a2233; }
.card-head { display: flex; justify-content: space-between; align-items: center; }
.level-toggle button { border: 1px solid #d3dae4; background: #fff; color: #445167; font-size: 12px; padding: 4px 10px; border-radius: 6px; margin-left: 6px; }
.level-toggle button.on { background: #0b6bcb; color: #fff; border-color: #0b6bcb; }

.map-wrap { position: relative; margin-top: 10px; }
.choropleth { height: 440px; border-radius: 8px; z-index: 0; }
.ramp { display: flex; align-items: center; gap: 3px; margin-top: 8px; font-size: 11px; color: #6b7787; }
.ramp i { width: 22px; height: 12px; display: inline-block; border-radius: 2px; }
.hint { margin-top: 6px; font-size: 12px; color: #98a4b3; }

.track-panel {
  position: absolute; top: 10px; right: 10px; width: 220px; max-height: 420px;
  background: rgba(255,255,255,.97); border: 1px solid #dde3ea; border-radius: 10px;
  box-shadow: 0 4px 16px rgba(16,34,60,.18); z-index: 500; display: flex; flex-direction: column;
}
.tp-head { display: flex; justify-content: space-between; align-items: center; padding: 10px 12px 4px; }
.tp-title { font-size: 14px; font-weight: 700; color: #0b2540; }
.tp-parent { color: #98a4b3; font-weight: 400; font-size: 12px; }
.tp-x { border: none; background: none; font-size: 20px; color: #98a4b3; cursor: pointer; line-height: 1; }
.tp-sub { padding: 0 12px 8px; font-size: 12px; color: #445167; border-bottom: 1px solid #eef1f5; }
.tp-showing { color: #98a4b3; margin-left: 6px; }
.tp-list { list-style: none; margin: 0; padding: 4px; overflow-y: auto; }
.tp-list li { display: flex; align-items: center; gap: 6px; padding: 5px 6px; border-radius: 6px; font-size: 12px; cursor: pointer; }
.tp-list li:hover, .tp-list li.on { background: #eaf3fc; }
.tp-list .dot { width: 9px; height: 9px; border-radius: 50%; flex: 0 0 auto; }
.tp-list .nm { flex: 1; color: #1a2233; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tp-list .yr { color: #6b7787; }
.tp-list .wd { color: #c0392b; font-weight: 600; }
</style>
