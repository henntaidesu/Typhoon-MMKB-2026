<template>
  <div class="stats">
    <div v-if="err" class="err">加载失败：{{ err }}</div>

    <!-- 汇总磁贴 -->
    <div class="tiles">
      <div class="tile"><div class="num">{{ summary.total_typhoons ?? '—' }}</div><div class="cap">{{ t('stats.totalTyphoons') }}</div></div>
      <div class="tile"><div class="num">{{ summary.total_landfalls ?? '—' }}</div><div class="cap">{{ t('stats.totalLandfalls') }}</div></div>
      <div class="tile"><div class="num">{{ summary.total_disasters ?? '—' }}</div><div class="cap">{{ t('stats.totalDisasters') }}</div></div>
    </div>

    <div class="cols">
      <!-- 左：登陆频次分级地图 -->
      <section class="card map-card">
        <div class="card-head">
          <h3>{{ t('stats.regionFrequency') }}</h3>
          <div class="level-toggle">
            <button :class="{ on: level === 0 }" @click="setLevel(0)">{{ t('stats.byCountry') }}</button>
            <button :class="{ on: level === 1 }" @click="setLevel(1)">{{ t('stats.byProvince') }}</button>
          </div>
        </div>
        <div ref="mapEl" class="choropleth"></div>
        <div class="ramp">
          <span>{{ t('stats.rampLow') }}</span>
          <i v-for="c in rampColors" :key="c" :style="{ background: c }"></i>
          <span>{{ t('stats.rampHigh') }} ({{ t('stats.maxTimes', { n: maxCount }) }})</span>
        </div>
      </section>

      <!-- 右：柱状统计 -->
      <section class="card">
        <h3>{{ t('stats.topCountries') }}</h3>
        <BarChart :items="countryBars" color="#c0392b" />
        <h3 style="margin-top:18px">{{ t('stats.topRegions') }}</h3>
        <BarChart :items="regionBars" color="#0b6bcb" />
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

const mapEl = ref(null)
let map = null
let choroLayer = null

// Sequential blue ramp (light→dark) for landfall frequency.
const rampColors = ['#eaf3fc', '#c6dbf0', '#8ebde0', '#4f95cf', '#1f6fb8', '#0b4a86']
function rampColor(count, max) {
  if (!count) return '#f3f5f8'
  const t = Math.min(1, count / Math.max(1, max))
  return rampColors[Math.min(rampColors.length - 1, Math.floor(t * rampColors.length))]
}

async function loadSummary() {
  const s = await api.stats()
  Object.assign(summary, s)
  yearBars.value = (s.typhoons_by_year || []).map((r) => ({ label: String(r.year), value: r.count }))
  typeBars.value = (s.disasters_by_type || []).map((r) => ({ label: r.type, value: r.count }))
  countryBars.value = (s.top_countries || []).map((r) => ({ label: r.country, value: r.count }))
}

async function loadRegionBars() {
  const rows = await api.statsByRegion({ level: level.value })
  regionBars.value = rows.slice(0, 12).map((r) => ({
    label: r.name + (r.country && level.value === 1 ? ` (${r.country})` : ''),
    value: r.landfall_count,
  }))
}

async function loadChoropleth() {
  if (!map) return
  const fc = await api.landfallGeojson({ level: level.value })
  maxCount.value = fc.properties?.max_landfall_count || 0
  if (choroLayer) { map.removeLayer(choroLayer); choroLayer = null }
  choroLayer = L.geoJSON(fc, {
    style: (f) => ({
      fillColor: rampColor(f.properties.landfall_count, maxCount.value),
      fillOpacity: 0.75, color: '#5b6b7f', weight: 0.6,
    }),
    onEachFeature: (f, layer) => {
      const p = f.properties
      layer.bindTooltip(
        `<b>${p.name || '—'}</b>${p.country ? ' · ' + p.country : ''}<br/>` +
        t('stats.tooltipLandfall', { lf: p.landfall_count, imp: p.impact_count }),
        { sticky: true },
      )
    },
  }).addTo(map)
}

function setLevel(l) {
  if (level.value === l) return
  level.value = l
}

watch(level, async () => {
  try { await Promise.all([loadRegionBars(), loadChoropleth()]) }
  catch (e) { err.value = String(e.message || e) }
})

onMounted(async () => {
  await nextTick()
  map = L.map(mapEl.value, { worldCopyJump: true }).setView([22, 130], 3)
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap', maxZoom: 10,
  }).addTo(map)
  try {
    await Promise.all([loadSummary(), loadRegionBars(), loadChoropleth()])
  } catch (e) {
    err.value = String(e.message || e)
  }
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

.choropleth { height: 420px; border-radius: 8px; margin-top: 10px; z-index: 0; }
.ramp { display: flex; align-items: center; gap: 3px; margin-top: 8px; font-size: 11px; color: #6b7787; }
.ramp i { width: 22px; height: 12px; display: inline-block; border-radius: 2px; }
</style>
