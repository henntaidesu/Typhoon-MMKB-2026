<template>
  <div v-if="t" class="detail">
    <div class="head">
      <h3>{{ t.name || $t('detail.unnamed') }} <span class="id">#{{ t.intl_id }}</span></h3>
      <button class="x" @click="store.selectedId = null">×</button>
    </div>
    <table>
      <tbody>
        <tr><td>{{ $t('detail.status') }}</td><td>
          <span class="status" :class="t.is_active ? 'live' : 'ended'">
            {{ t.is_active ? $t('detail.active') : $t('detail.ended') }}
          </span>
        </td></tr>
        <tr><td>{{ $t('detail.season') }}</td><td>{{ t.season_year }}</td></tr>
        <tr><td>{{ $t('detail.category') }}</td><td>{{ t.category || '—' }}</td></tr>
        <tr><td>{{ $t('detail.peakWind') }}</td><td>{{ t.max_wind_kt ?? '?' }} kt</td></tr>
        <tr><td>{{ $t('detail.minPressure') }}</td><td>{{ t.min_pressure_hpa ?? '?' }} hPa</td></tr>
        <tr><td>{{ $t('detail.period') }}</td><td>{{ fmt(t.start_time) }} → {{ fmt(t.end_time) }}</td></tr>
      </tbody>
    </table>

    <h4>{{ $t('detail.intensityCurve') }}</h4>
    <svg class="spark" viewBox="0 0 300 80" preserveAspectRatio="none">
      <polyline :points="windPath" fill="none" stroke="#c0392b" stroke-width="2" />
      <polyline :points="presPath" fill="none" stroke="#0b6bcb" stroke-width="1.5" />
    </svg>
    <div class="legend"><span class="w">— {{ $t('detail.wind') }}</span><span class="p">— {{ $t('detail.pressure') }}</span></div>

    <h4>{{ $t('detail.affectedRegions') }} ({{ countries.length }})</h4>
    <ul class="regs">
      <li v-for="c in countries" :key="c.admin_region_id">
        <span class="rn">{{ c.name }}</span>
        <span v-if="c.country && c.admin_level === 1" class="parent">· {{ c.country }}</span>
        <span v-if="c.landfall" class="lf">{{ $t('detail.landfall') }}</span>
      </li>
      <li v-if="!countries.length" class="empty">{{ $t('detail.noRecords') }}</li>
    </ul>

    <h4>{{ $t('detail.disasters') }} ({{ disasterCount }})</h4>
    <ul class="dis">
      <li v-for="d in disasters" :key="d.properties.id">
        <b>{{ d.properties.disaster_type }}</b>
        <span v-if="d.properties.casualties"> · {{ $t('detail.casualties') }} {{ d.properties.casualties }}</span>
        <div class="d">{{ (d.properties.description || '').slice(0, 120) }}</div>
      </li>
      <li v-if="!disasters.length" class="empty">{{ $t('detail.noRecords') }}</li>
    </ul>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useTyphoonStore } from '../stores/typhoon'
const store = useTyphoonStore()
const t = computed(() => store.selected)
const pts = computed(() => store.trackPoints)
const disasters = computed(() => store.disasters?.features || [])
const disasterCount = computed(() => disasters.value.length)
const countries = computed(() => store.countries || [])

function fmt(s) { return s ? s.slice(0, 10) : '—' }

function scalePath(key, lo, hi) {
  const arr = pts.value
  if (!arr.length) return ''
  return arr.map((p, i) => {
    const x = (i / Math.max(arr.length - 1, 1)) * 300
    const v = p[key]
    const y = v == null ? 80 : 80 - ((v - lo) / (hi - lo)) * 76
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
}
const windPath = computed(() => scalePath('wind_kt', 0, 160))
const presPath = computed(() => scalePath('pressure_hpa', 880, 1010))
</script>

<style scoped>
.detail { padding: 14px; overflow-y: auto; height: 100%; }
.head { display: flex; justify-content: space-between; align-items: center; }
h3 { margin: 0; font-size: 17px; }
.id { color: #98a4b3; font-weight: 400; font-size: 13px; }
.x { border: none; background: none; font-size: 22px; color: #98a4b3; }
h4 { margin: 16px 0 6px; font-size: 13px; color: #33435a; }
table { width: 100%; font-size: 13px; border-collapse: collapse; }
td { padding: 4px 0; }
td:first-child { color: #6b7787; width: 78px; }
.status.live { color: #e0392b; font-weight: 700; }
.status.ended { color: #6b7787; }
.spark { width: 100%; height: 80px; background: #f4f7fb; border-radius: 6px; }
.legend { font-size: 11px; margin-top: 4px; }
.legend .w { color: #c0392b; margin-right: 10px; }
.legend .p { color: #0b6bcb; }
.dis { list-style: none; padding: 0; margin: 6px 0 0; }
.dis li { padding: 7px 0; border-bottom: 1px solid #eef1f5; font-size: 13px; }
.dis .d { color: #6b7787; font-size: 12px; margin-top: 2px; }
.dis .empty { color: #98a4b3; }
.regs { list-style: none; padding: 0; margin: 6px 0 0; display: flex; flex-wrap: wrap; gap: 6px; }
.regs li { font-size: 12px; background: #f0f4f9; border-radius: 6px; padding: 3px 8px; color: #33435a; }
.regs .parent { color: #98a4b3; margin-left: 3px; }
.regs .lf { margin-left: 5px; background: #c0392b; color: #fff; border-radius: 4px; padding: 0 5px; font-size: 11px; }
.regs .empty { background: none; color: #98a4b3; padding: 4px 0; }
</style>
