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
        </div>

        <div class="ramp" v-show="!selected">
          <span>{{ t('stats.rampLow') }}</span>
          <i v-for="c in rampColors" :key="c" :style="{ background: c }"></i>
          <span>{{ t('stats.rampHigh') }} ({{ t('stats.maxTimes', { n: maxCount }) }})</span>
        </div>
        <div class="hint" v-show="!selected">{{ t('stats.clickHint') }}</div>

        <!-- 单台风轨迹回放时间轴（点选列表中的台风后出现，在地图下方） -->
        <div v-if="playbackTrack" class="timeline">
          <button class="tl-play" @click="togglePlay" :title="playing ? t('timeline.pause') : t('timeline.play')">
            {{ playing ? '⏸' : '▶' }}
          </button>
          <input type="range" min="0" :max="Math.max(0, playbackPoints.length - 1)"
                 :value="playIndex" @input="setPlayIndex(+$event.target.value)" />
          <div class="tl-label">
            <b>{{ playbackName }}</b>
            <span>· {{ playbackCurrent?.time?.slice(0, 16) || '' }}</span>
            <span>· {{ playbackCurrent?.wind_kt ?? '?' }} kt</span>
            <span>· {{ playbackCurrent?.pressure_hpa ?? '?' }} hPa</span>
          </div>
          <button class="tl-x" @click="clearPlayback" title="×">×</button>
        </div>

        <!-- 相关台风面板：筛选 + 列表，移到地图下方（左侧列内） -->
        <div v-if="selected" class="region-panel">
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
            <span class="tp-showing">({{ filteredFeatures.length }})</span>
          </div>

          <!-- 筛选：年份 / 强度 / 仅登陆 -->
          <div class="tp-filters">
            <select v-model="filterYear" class="tp-sel">
              <option value="">{{ t('stats.allYears') }}</option>
              <option v-for="y in availableYears" :key="y" :value="y">{{ y }}</option>
            </select>
            <select v-model.number="filterMinKt" class="tp-sel">
              <option v-for="o in intensityOptions" :key="o.kt" :value="o.kt">{{ o.label }}</option>
            </select>
            <button class="tp-lf" :class="{ on: landfallOnly }" @click="landfallOnly = !landfallOnly">
              {{ t('stats.landfallOnly') }}
            </button>
          </div>

          <ul class="tp-list">
            <template v-for="f in displayFeatures" :key="f.properties.typhoon_id">
              <li :class="{ on: hoverId === f.properties.typhoon_id, active: playbackId === f.properties.typhoon_id }"
                  @mouseenter="highlight(f.properties.typhoon_id)"
                  @mouseleave="highlight(null)"
                  @click="selectTyphoon(f.properties.typhoon_id)">
                <span class="dot" :style="{ background: windColor(f.properties.max_wind_kt) }"></span>
                <span class="nm">{{ f.properties.name || f.properties.intl_id }}</span>
                <span class="yr">{{ f.properties.season_year }}</span>
                <span class="wd">{{ f.properties.max_wind_kt ?? '?' }}kt</span>
                <span class="play-hint">{{ playbackId === f.properties.typhoon_id ? '▶' : '' }}</span>
              </li>
              <!-- 选中行下方内嵌的时间轴 -->
              <li v-if="playbackTrack && playbackId === f.properties.typhoon_id" class="tp-tl-row">
                <button class="tl-play" @click.stop="togglePlay"
                        :title="playing ? t('timeline.pause') : t('timeline.play')">
                  {{ playing ? '⏸' : '▶' }}
                </button>
                <input type="range" min="0" :max="Math.max(0, playbackPoints.length - 1)"
                       :value="playIndex" @click.stop @input="setPlayIndex(+$event.target.value)" />
                <span class="tp-tl-label">
                  {{ playbackCurrent?.time?.slice(0, 16) || '' }}
                  · {{ playbackCurrent?.wind_kt ?? '?' }}kt
                  · {{ playbackCurrent?.pressure_hpa ?? '?' }}hPa
                </span>
              </li>
            </template>
            <li v-if="!filteredFeatures.length" class="tp-empty">{{ t('stats.noData') }}</li>
          </ul>
        </div>
      </section>

      <!-- 右：柱状统计（切换显示 + 排序 + 懒加载） -->
      <section class="card">
        <!-- 切换按钮：选择显示哪个统计 -->
        <div class="chart-tabs">
          <button v-for="tb in TABS" :key="tb.key"
                  :class="{ on: activeTab === tb.key }" @click="setTab(tb.key)">
            {{ tb.label }}
          </button>
        </div>

        <div class="chart-head">
          <h3>{{ activeTabLabel }}</h3>
          <div class="sort-toggle">
            <button :class="{ on: sortDir === 'desc' }" @click="setSort('desc')">{{ t('stats.sortDesc') }}</button>
            <button :class="{ on: sortDir === 'asc' }" @click="setSort('asc')">{{ t('stats.sortAsc') }}</button>
          </div>
        </div>

        <!-- 限高滚动容器：下拉到底部时懒加载更多 -->
        <div class="chart-scroll" ref="chartScrollEl" @scroll="onChartScroll">
          <BarChart :items="shownItems" :color="activeColor" :max="activeMax"
                    :active-id="activeRegionId" @select="onBar" />
          <div v-if="shownItems.length < activeItems.length" class="load-more">
            {{ shownItems.length }} / {{ activeItems.length }} …
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
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

// --- 右侧统计：切换显示 + 排序 + 懒加载 ---
const TABS = computed(() => [
  { key: 'country', label: t('stats.topCountries'), color: '#c0392b' },
  { key: 'region', label: t('stats.topRegions'), color: '#0b6bcb' },
  { key: 'year', label: t('stats.typhoonsByYear'), color: '#2e9e5b' },
  { key: 'type', label: t('stats.disasterTypes'), color: '#e67e22' },
])
const activeTab = ref('country')
const sortDir = ref('desc')          // 'desc' | 'asc'
const PAGE = 15
const visibleCount = ref(PAGE)
const chartScrollEl = ref(null)

const activeData = computed(() => ({
  country: countryBars.value, region: regionBars.value,
  year: yearBars.value, type: typeBars.value,
}[activeTab.value] || []))
// Fixed scale so bar widths don't rescale when sorting ascending / lazy-loading.
const activeMax = computed(() => Math.max(1, ...activeData.value.map((i) => i.value || 0)))
const activeItems = computed(() => {
  const arr = [...activeData.value]
  arr.sort((a, b) => sortDir.value === 'asc' ? (a.value - b.value) : (b.value - a.value))
  return arr
})
const shownItems = computed(() => activeItems.value.slice(0, visibleCount.value))
const activeColor = computed(() => TABS.value.find((tb) => tb.key === activeTab.value)?.color)
const activeTabLabel = computed(() => TABS.value.find((tb) => tb.key === activeTab.value)?.label)

function resetLazy() {
  visibleCount.value = PAGE
  if (chartScrollEl.value) chartScrollEl.value.scrollTop = 0
}
function setTab(k) { if (activeTab.value !== k) { activeTab.value = k; resetLazy() } }
function setSort(d) { if (sortDir.value !== d) { sortDir.value = d; resetLazy() } }
function onChartScroll(e) {
  const el = e.target
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 48
      && visibleCount.value < activeItems.value.length) {
    visibleCount.value += PAGE
  }
}
// If the rendered slice doesn't fill (overflow) the box, keep revealing rows so
// there's always something to scroll to — otherwise lazy loading can't trigger.
watch(shownItems, async () => {
  await nextTick()
  const el = chartScrollEl.value
  if (el && el.scrollHeight <= el.clientHeight && visibleCount.value < activeItems.value.length) {
    visibleCount.value += PAGE
  }
})

let suppressLandfallWatch = false  // guards the landfallOnly watcher during selectRegion

const selected = ref(null)     // { region_id, region_name, parent_name, typhoon_count }
const selectedId = ref(null)   // currently selected admin_region id (for re-fetch)
const allFeatures = ref([])    // every related typhoon track returned by the API
const hoverId = ref(null)
const activeRegionId = ref(null)

// --- 相关台风面板的筛选状态 ---
const filterYear = ref('')     // '' = 全部年份
const filterMinKt = ref(0)     // 0 = 全部强度；否则为最小 max_wind_kt 阈值
const landfallOnly = ref(false)

// Saffir-Simpson-ish minimum-intensity thresholds for the dropdown.
const intensityOptions = computed(() => [
  { kt: 0, label: t('stats.allIntensity') },
  { kt: 34, label: '≥ TS' },
  { kt: 64, label: '≥ Cat1' },
  { kt: 96, label: '≥ Cat3' },
  { kt: 113, label: '≥ Cat4' },
])

// Years present in the current region's storms, newest first (drives the dropdown).
const availableYears = computed(() => {
  const ys = new Set()
  for (const f of allFeatures.value) {
    if (f.properties.season_year != null) ys.add(f.properties.season_year)
  }
  return [...ys].sort((a, b) => b - a)
})

// API already returns tracks sorted year-desc, so slicing keeps "the latest N".
const filteredFeatures = computed(() => allFeatures.value.filter((f) => {
  const p = f.properties
  if (filterYear.value !== '' && p.season_year !== filterYear.value) return false
  if (filterMinKt.value && (p.max_wind_kt == null || p.max_wind_kt < filterMinKt.value)) return false
  return true
}))

// 直接展示全部筛选结果（不再默认只显示前 5 条）。
const displayFeatures = computed(() => filteredFeatures.value)

const mapEl = ref(null)
let map = null
let choroLayer = null
let tracksLayer = null

// --- 单台风轨迹回放（点选列表中的台风 → 在地图上高亮其轨迹并可沿时间轴播放）---
const playbackId = ref(null)      // 正在回放的 typhoon_id
const playbackTrack = ref(null)   // getTrack 返回的带时间戳轨迹 Feature
const playIndex = ref(0)
const playing = ref(false)
let playTimer = null
let playbackLayer = null          // 加粗着色轨迹
let playCursor = null             // 移动光标

const playbackPoints = computed(() => playbackTrack.value?.properties?.points || [])
const playbackCurrent = computed(() => playbackPoints.value[playIndex.value])
const playbackName = computed(() => {
  const f = allFeatures.value.find((x) => x.properties.typhoon_id === playbackId.value)
  return f ? (f.properties.name || f.properties.intl_id) : ''
})

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

// Request sequencing, same reason as in the typhoon store: these fetches are
// slow (the level-2 choropleth is ~1s) and the user can switch level or region
// mid-flight. Without a token the slower earlier response lands last and the
// view ends up labelled one thing while showing another.
let levelSeq = 0    // the admin level: bar list + choropleth
let regionSeq = 0   // the selected region's storm list
let trackSeq = 0    // the single-storm playback track

async function loadSummary() {
  const s = await api.stats()
  Object.assign(summary, s)
  yearBars.value = (s.typhoons_by_year || []).map((r) => ({ label: String(r.year), value: r.count }))
  typeBars.value = (s.disasters_by_type || []).map((r) => ({ label: r.type, value: r.count }))
}

async function loadCountryBars() {
  const rows = await api.statsByCountry()
  countryBars.value = rows.map((r) => ({
    label: r.country, value: r.typhoon_count, id: r.admin_region_id,
  }))
}

async function loadRegionBars(seq) {
  // Capture the level this request is for: reading level.value again when the
  // response arrives would label the rows against whatever level is current by
  // then, not the one they describe.
  const lvl = level.value === 0 ? 1 : level.value
  const rows = await api.statsByRegion({ level: lvl })
  if (seq !== levelSeq) return
  regionBars.value = rows.map((r) => ({
    label: r.name + (r.parent_name ? ` (${r.parent_name})` : r.country && lvl !== 0 ? ` (${r.country})` : ''),
    value: r.landfall_count, id: r.admin_region_id,
  }))
}

async function loadChoropleth(seq) {
  if (!map) return
  const fc = await api.landfallGeojson({ level: level.value })
  if (seq !== levelSeq) return
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

// Fetch the region's storms; landfall_only is the only server-side filter
// (year/intensity are applied client-side for instant response).
async function fetchTracks(fallbackName, seq = ++regionSeq) {
  const fc = await api.regionTracks(selectedId.value, { landfall_only: landfallOnly.value })
  // A different region was clicked while this was in flight; writing now would
  // list this region's storms under that region's heading.
  if (seq !== regionSeq) return
  allFeatures.value = fc.features
  selected.value = { ...fc.properties, region_name: fc.properties.region_name || fallbackName }
}

async function selectRegion(regionId, name, opts = {}) {
  if (regionId == null) return
  selectedId.value = regionId
  activeRegionId.value = regionId
  // Reset filters so a fresh region defaults to "all years, all intensities".
  filterYear.value = ''
  filterMinKt.value = 0
  // Selecting from a landfall-ranked source (上陸が多い地域) should list the
  // storms that LANDED here — matching the bar's landfall count — not every
  // storm that merely passed through. Suppress the landfallOnly watcher so this
  // change doesn't trigger a second fetch on top of the one below.
  suppressLandfallWatch = true
  landfallOnly.value = !!opts.landfallOnly
  suppressLandfallWatch = false
  clearPlayback()
  try {
    await fetchTracks(name)
    if (choroLayer) choroLayer.setStyle(choroStyle)
  } catch (e) { err.value = String(e.message || e) }
}

function drawTracks() {
  if (tracksLayer) { map.removeLayer(tracksLayer); tracksLayer = null }
  // 选中单台风回放时，地图只保留该台风的轨迹（drawPlayback 负责），隐藏区域内其它淡色轨迹。
  if (playbackId.value != null) return
  if (!displayFeatures.value.length) return
  tracksLayer = L.geoJSON({ type: 'FeatureCollection', features: displayFeatures.value }, {
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
  // Bump the token: a region fetch still in flight would otherwise land after
  // this and repopulate the panel the user just dismissed.
  regionSeq += 1
  clearPlayback()
  selected.value = null
  selectedId.value = null
  allFeatures.value = []
  activeRegionId.value = null
  if (tracksLayer) { map.removeLayer(tracksLayer); tracksLayer = null }
  if (choroLayer) choroLayer.setStyle(choroStyle)
}

// Fetch the clicked storm's full timed track; the map then shows ONLY that
// storm (the faint region tracks are hidden while a storm is selected).
async function selectTyphoon(tid) {
  if (playbackId.value === tid) { clearPlayback(); return }  // click again → toggle off
  stopPlay()
  try {
    const seq = ++trackSeq
    const tr = await api.getTrack(tid)
    if (seq !== trackSeq) return  // a different storm was clicked meanwhile
    playbackId.value = tid
    playbackTrack.value = tr
    playIndex.value = 0
    drawTracks()    // playbackId set → clears the faint region tracks
    drawPlayback()  // draw the selected storm's bold track + cursor
    const coords = tr.geometry?.coordinates || []
    if (coords.length) map.fitBounds(L.latLngBounds(coords.map((c) => [c[1], c[0]])).pad(0.25))
  } catch (e) { err.value = String(e.message || e) }
}

function drawPlayback() {
  if (playbackLayer) { map.removeLayer(playbackLayer); playbackLayer = null }
  if (playCursor) { map.removeLayer(playCursor); playCursor = null }
  const tr = playbackTrack.value
  if (!tr) return
  const coords = tr.geometry.coordinates
  const pts = tr.properties.points
  const group = L.layerGroup()
  // Segment coloured by the wind at each segment's start point (like MapView).
  for (let i = 0; i < coords.length - 1; i++) {
    L.polyline([[coords[i][1], coords[i][0]], [coords[i + 1][1], coords[i + 1][0]]],
      { color: windColor(pts[i]?.wind_kt), weight: 4, opacity: 0.95 }).addTo(group)
  }
  group.addTo(map)
  playbackLayer = group
  updateCursor()
}

function updateCursor() {
  const tr = playbackTrack.value
  const c = tr?.geometry?.coordinates?.[playIndex.value]
  if (!c) return
  const w = tr.properties.points[playIndex.value]?.wind_kt
  const latlng = [c[1], c[0]]
  if (!playCursor) {
    playCursor = L.circleMarker(latlng, {
      radius: 8, color: '#111', weight: 2, fillColor: windColor(w), fillOpacity: 0.95,
    }).addTo(map)
  } else {
    playCursor.setLatLng(latlng).setStyle({ fillColor: windColor(w) })
  }
}

function setPlayIndex(i) { playIndex.value = i; updateCursor() }

function togglePlay() {
  playing.value = !playing.value
  if (playing.value) {
    if (playIndex.value >= playbackPoints.value.length - 1) setPlayIndex(0)
    playTimer = setInterval(() => {
      if (playIndex.value >= playbackPoints.value.length - 1) { stopPlay(); return }
      setPlayIndex(playIndex.value + 1)
    }, 250)
  } else { stopPlay() }
}

function stopPlay() {
  playing.value = false
  if (playTimer) { clearInterval(playTimer); playTimer = null }
}

function clearPlayback() {
  stopPlay()
  // Same reason as clearSelection: a track fetch in flight must not resurrect
  // the playback that was just closed.
  trackSeq += 1
  const wasActive = playbackId.value != null
  playbackId.value = null
  playbackTrack.value = null
  playIndex.value = 0
  if (playbackLayer) { map.removeLayer(playbackLayer); playbackLayer = null }
  if (playCursor) { map.removeLayer(playCursor); playCursor = null }
  // Restore the faint region tracks that were hidden during playback.
  if (wasActive) drawTracks()
}

// Redraw whenever the visible subset changes (region select, year/intensity
// filter, or show-more toggle). fitBounds re-frames the map to the matches.
watch(displayFeatures, () => {
  // If the storm being played is filtered out of view, stop the playback.
  if (playbackId.value != null &&
      !displayFeatures.value.some((f) => f.properties.typhoon_id === playbackId.value)) {
    clearPlayback()
  }
  drawTracks()
})

// Landfall-only is server-side, so re-fetch; keep year/intensity filters intact.
// Suppressed while selectRegion sets it (that path fetches once itself).
watch(landfallOnly, async () => {
  if (selectedId.value == null || suppressLandfallWatch) return
  try { await fetchTracks(selected.value?.region_name) }
  catch (e) { err.value = String(e.message || e) }
}, { flush: 'sync' })

// 上陸が多い地域 ranks by landfall count → show only the storms that landed
// here; 影響を受けた国 ranks by total impact → show all storms that affected it.
function onBar(item) { selectRegion(item.id, item.label, { landfallOnly: activeTab.value === 'region' }) }

function setLevel(l) {
  if (level.value === l) return
  level.value = l
  clearSelection()
}

// One token for the pair: the bar list and the choropleth describe the same
// level, so a stale response to either must be dropped together.
async function reloadLevel() {
  const seq = ++levelSeq
  await Promise.all([loadRegionBars(seq), loadChoropleth(seq)])
}

watch(level, async () => {
  try { await reloadLevel() }
  catch (e) { err.value = String(e.message || e) }
})

onMounted(async () => {
  await nextTick()
  map = L.map(mapEl.value, { worldCopyJump: true }).setView([22, 128], 3)
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap', maxZoom: 10,
  }).addTo(map)
  try {
    await Promise.all([loadSummary(), loadCountryBars(), reloadLevel()])
  } catch (e) { err.value = String(e.message || e) }
})
onUnmounted(() => { stopPlay(); if (map) { map.remove(); map = null } })
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

/* 右侧统计：切换标签 + 排序 + 限高滚动 */
.chart-tabs { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
.chart-tabs button {
  border: 1px solid #d3dae4; background: #fff; color: #445167; font-size: 12px;
  padding: 5px 10px; border-radius: 999px; cursor: pointer;
}
.chart-tabs button:hover { background: #f0f4f9; }
.chart-tabs button.on { background: #0b2540; color: #fff; border-color: #0b2540; }
.chart-head { display: flex; justify-content: space-between; align-items: center; }
.sort-toggle button {
  border: 1px solid #d3dae4; background: #fff; color: #445167; font-size: 12px;
  padding: 3px 10px; cursor: pointer;
}
.sort-toggle button:first-child { border-radius: 6px 0 0 6px; }
.sort-toggle button:last-child { border-radius: 0 6px 6px 0; border-left: none; }
.sort-toggle button.on { background: #0b6bcb; color: #fff; border-color: #0b6bcb; }
.chart-scroll { max-height: 440px; overflow-y: auto; margin-top: 10px; padding-right: 4px; }
.load-more { text-align: center; font-size: 12px; color: #98a4b3; padding: 8px 0 4px; }

.map-wrap { position: relative; margin-top: 10px; }
.choropleth { height: 440px; border-radius: 8px; z-index: 0; }
.ramp { display: flex; align-items: center; gap: 3px; margin-top: 8px; font-size: 11px; color: #6b7787; }
.ramp i { width: 22px; height: 12px; display: inline-block; border-radius: 2px; }
.hint { margin-top: 6px; font-size: 12px; color: #98a4b3; }

/* 相关台风面板：改为地图下方的常规块（不再浮在地图上）。 */
.region-panel {
  margin-top: 12px; background: #fff; border: 1px solid #dde3ea; border-radius: 10px;
  display: flex; flex-direction: column;
}

/* 单台风回放时间轴：地图下方的常规块。 */
.timeline {
  margin-top: 12px; display: flex; align-items: center; gap: 12px;
  background: #f5f8fc; border: 1px solid #dde3ea; border-radius: 10px; padding: 8px 12px;
}
.timeline .tl-play {
  width: 32px; height: 32px; flex: 0 0 auto; border: none; border-radius: 8px;
  background: #0b6bcb; color: #fff; font-size: 13px; cursor: pointer;
}
.timeline input[type=range] { flex: 1; min-width: 0; }
.tl-label { font-size: 12px; color: #33435a; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tl-label b { color: #0b2540; }
.tl-x { flex: 0 0 auto; border: none; background: none; font-size: 20px; color: #98a4b3; cursor: pointer; line-height: 1; }
.tp-filters { display: flex; flex-wrap: wrap; gap: 6px; padding: 8px 12px; border-bottom: 1px solid #eef1f5; }
.tp-sel { flex: 1 1 88px; min-width: 0; font-size: 12px; color: #445167; padding: 3px 6px;
  border: 1px solid #d3dae4; border-radius: 6px; background: #fff; }
.tp-lf { flex: 0 0 auto; font-size: 12px; padding: 3px 10px; border: 1px solid #d3dae4;
  border-radius: 6px; background: #fff; color: #445167; cursor: pointer; }
.tp-lf.on { background: #0b6bcb; color: #fff; border-color: #0b6bcb; }
.tp-empty { justify-content: center; color: #98a4b3; font-size: 12px; padding: 10px 6px; }
.tp-more { margin: 6px 10px 10px; padding: 5px 0; font-size: 12px; color: #0b6bcb;
  background: #f2f7fd; border: 1px solid #d5e6f7; border-radius: 6px; cursor: pointer; }
.tp-more:hover { background: #e7f1fb; }
.tp-head { display: flex; justify-content: space-between; align-items: center; padding: 10px 12px 4px; }
.tp-title { font-size: 14px; font-weight: 700; color: #0b2540; }
.tp-parent { color: #98a4b3; font-weight: 400; font-size: 12px; }
.tp-x { border: none; background: none; font-size: 20px; color: #98a4b3; cursor: pointer; line-height: 1; }
.tp-sub { padding: 0 12px 8px; font-size: 12px; color: #445167; border-bottom: 1px solid #eef1f5; }
.tp-showing { color: #98a4b3; margin-left: 6px; }
.tp-list { list-style: none; margin: 0; padding: 4px; overflow-y: auto; max-height: 320px; }
.tp-list li { display: flex; align-items: center; gap: 6px; padding: 5px 6px; border-radius: 6px; font-size: 12px; cursor: pointer; }
.tp-list li:hover, .tp-list li.on { background: #eaf3fc; }
.tp-list li.active { background: #dcebfb; box-shadow: inset 2px 0 0 #0b6bcb; }
.tp-list .dot { width: 9px; height: 9px; border-radius: 50%; flex: 0 0 auto; }
.tp-list .nm { flex: 1; color: #1a2233; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tp-list .yr { color: #6b7787; }
.tp-list .wd { color: #c0392b; font-weight: 600; }
.tp-list .play-hint { color: #0b6bcb; font-size: 10px; width: 10px; flex: 0 0 auto; }
/* 列表内嵌时间轴行 */
.tp-tl-row { display: flex; align-items: center; gap: 8px; padding: 6px 6px 8px 6px; cursor: default; }
.tp-tl-row:hover { background: transparent; }
.tp-tl-row .tl-play {
  width: 26px; height: 26px; flex: 0 0 auto; border: none; border-radius: 7px;
  background: #0b6bcb; color: #fff; font-size: 12px; cursor: pointer;
}
.tp-tl-row input[type=range] { flex: 1; min-width: 0; }
.tp-tl-label { font-size: 11px; color: #566; white-space: nowrap; }
</style>
