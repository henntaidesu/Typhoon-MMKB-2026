<template>
  <div class="list">
    <div class="filters">
      <input v-model.number="store.filters.year" type="number" :placeholder="$t('list.yearPlaceholder')" @keyup.enter="store.loadList()" />
      <input v-model="store.filters.name" :placeholder="$t('list.namePlaceholder')" @keyup.enter="store.loadList()" />
      <button @click="store.loadList()">{{ $t('list.filter') }}</button>
    </div>
    <div v-if="store.loading" class="msg">{{ $t('list.loading') }}</div>
    <div v-else-if="store.error" class="msg err">{{ store.error }}</div>
    <template v-else>
      <!-- 进行中：平铺 -->
      <section v-show="active.length" class="group">
        <h4 class="group-head active">
          <span class="gdot active"></span>{{ $t('list.active') }}<span class="count">{{ active.length }}</span>
        </h4>
        <ul>
          <li v-for="t in active" :key="t.id"
              :class="{ sel: t.id === store.selectedId, hit: store.semanticHits.has(t.id) }"
              @click="store.select(t.id)">
            <div class="name">
              <span class="dot" :style="{ background: catColor(t.max_wind_kt) }"></span>
              {{ t.name || $t('list.unnamed') }} <span class="id">#{{ t.intl_id }}</span>
            </div>
            <div class="meta">
              {{ t.category || '—' }} · {{ $t('list.peak') }} {{ t.max_wind_kt ?? '?' }} kt
              <span v-if="t.distance != null" class="sim">{{ $t('list.semanticDistance') }} {{ t.distance }}</span>
            </div>
            <div class="dates">
              {{ fmtDate(t.start_time) }} · <span class="live">{{ $t('list.untilNow') }} {{ fmtDate(t.end_time, true) }}</span>
            </div>
          </li>
        </ul>
      </section>

      <!-- 已结束：按年份分组，可收缩 -->
      <section v-show="endedTotal" class="group">
        <h4 class="group-head ended">
          <span class="gdot ended"></span>{{ $t('list.ended') }}<span class="count">{{ endedTotal }}</span>
        </h4>
        <div class="year-tools" v-if="endedByYear.length > 1">
          <button @click="setAll(true)">{{ $t('list.expandAll') }}</button>
          <button @click="setAll(false)">{{ $t('list.collapseAll') }}</button>
        </div>
        <div v-for="yg in endedByYear" :key="yg.year" class="year-group">
          <button class="year-head" @click="toggleYear(yg.year)">
            <span class="chev">{{ isYearOpen(yg.year) ? '▾' : '▸' }}</span>
            <template v-if="yg.year === UNKNOWN_YEAR">{{ $t('list.unknownYear') }}</template>
            <template v-else>{{ yg.year }}<span class="yunit">{{ $t('list.yearUnit') }}</span></template>
            <span class="count">{{ yg.items.length }}</span>
          </button>
          <ul v-show="isYearOpen(yg.year)">
            <li v-for="t in yg.items" :key="t.id"
                :class="{ sel: t.id === store.selectedId, hit: store.semanticHits.has(t.id) }"
                @click="store.select(t.id)">
              <div class="name">
                <span class="dot" :style="{ background: catColor(t.max_wind_kt) }"></span>
                {{ t.name || $t('list.unnamed') }} <span class="id">#{{ t.intl_id }}</span>
              </div>
              <div class="meta">
                {{ t.category || '—' }} · {{ $t('list.peak') }} {{ t.max_wind_kt ?? '?' }} kt
                <span v-if="t.distance != null" class="sim">{{ $t('list.semanticDistance') }} {{ t.distance }}</span>
              </div>
              <div class="dates">{{ fmtDate(t.start_time) }} → {{ fmtDate(t.end_time) }}</div>
            </li>
          </ul>
        </div>
      </section>
    </template>
    <div v-if="!store.loading && !store.list.length" class="msg">{{ $t('list.noData') }}</div>
  </div>
</template>

<script setup>
import { computed, reactive } from 'vue'
import { useTyphoonStore } from '../stores/typhoon'
const store = useTyphoonStore()

// Sentinel for typhoons without a season year; rendered via $t('list.unknownYear').
const UNKNOWN_YEAR = '__unknown__'

const active = computed(() => store.list.filter((t) => t.is_active))
const ended = computed(() => store.list.filter((t) => !t.is_active))
const endedTotal = computed(() => ended.value.length)

// Ended typhoons grouped by season year, newest year first.
const endedByYear = computed(() => {
  const m = new Map()
  for (const t of ended.value) {
    const y = t.season_year ?? UNKNOWN_YEAR
    if (!m.has(y)) m.set(y, [])
    m.get(y).push(t)
  }
  return [...m.entries()]
    .map(([year, items]) => ({ year, items }))
    .sort((a, b) => {
      if (a.year === UNKNOWN_YEAR) return 1
      if (b.year === UNKNOWN_YEAR) return -1
      return b.year - a.year
    })
})

// Per-year collapse state. Default: newest year open, the rest collapsed.
const expanded = reactive({})
function isYearOpen(y) {
  if (y in expanded) return expanded[y]
  return endedByYear.value.length > 0 && endedByYear.value[0].year === y
}
function toggleYear(y) { expanded[y] = !isYearOpen(y) }
function setAll(open) { for (const yg of endedByYear.value) expanded[yg.year] = open }

function fmtDate(s, withTime = false) {
  if (!s) return '—'
  return withTime ? s.slice(0, 16).replace('T', ' ') : s.slice(0, 10)
}
function catColor(kt) {
  if (kt == null) return '#8aa0b6'
  if (kt >= 113) return '#c0392b'
  if (kt >= 83) return '#e67e22'
  if (kt >= 64) return '#f39c12'
  if (kt >= 34) return '#2ecc71'
  return '#3498db'
}
</script>

<style scoped>
.list { flex: 1; overflow-y: auto; }
.filters { display: flex; gap: 6px; padding: 10px 12px; border-bottom: 1px solid #dde3ea; }
.filters input { width: 0; flex: 1; padding: 6px 8px; border: 1px solid #c6cfda; border-radius: 6px; }
.filters button { padding: 6px 10px; border: none; border-radius: 6px; background: #33435a; color: #fff; }

.group-head {
  display: flex; align-items: center; gap: 6px; margin: 0;
  padding: 8px 12px; font-size: 12px; font-weight: 700; color: #46586f;
  background: #eef3f9; border-bottom: 1px solid #e3e9f0;
  position: sticky; top: 0; z-index: 2;
}
.group-head .count { margin-left: auto; color: #98a4b3; font-weight: 600; }
.gdot { width: 8px; height: 8px; border-radius: 50%; }
.gdot.active { background: #e0392b; animation: pulse 1.6s infinite; }
.gdot.ended { background: #98a4b3; }
@keyframes pulse {
  0% { box-shadow: 0 0 0 0 rgba(224,57,43,.55); }
  70% { box-shadow: 0 0 0 6px rgba(224,57,43,0); }
  100% { box-shadow: 0 0 0 0 rgba(224,57,43,0); }
}

.year-tools { display: flex; gap: 8px; padding: 6px 12px; border-bottom: 1px solid #eef1f5; }
.year-tools button { border: none; background: none; color: var(--accent); font-size: 12px; padding: 2px 4px; }
.year-tools button:hover { text-decoration: underline; }

.year-group { border-bottom: 1px solid #eef1f5; }
.year-head {
  display: flex; align-items: center; gap: 6px; width: 100%;
  padding: 7px 12px; border: none; background: #fafcfe; text-align: left;
  font-size: 13px; font-weight: 600; color: #33435a;
}
.year-head:hover { background: #f1f6fc; }
.year-head .chev { width: 12px; color: #8a97a8; font-size: 11px; }
.year-head .yunit { color: #98a4b3; font-weight: 400; margin-left: 1px; }
.year-head .count { margin-left: auto; color: #98a4b3; font-weight: 600; }

ul { list-style: none; margin: 0; padding: 0; }
li { padding: 9px 12px 9px 20px; border-top: 1px solid #f1f4f8; cursor: pointer; }
li:hover { background: #f4f7fb; }
li.sel { background: #e3effb; }
li.hit { border-left: 3px solid var(--accent); }
.name { font-weight: 600; font-size: 14px; }
.id { color: #98a4b3; font-weight: 400; font-size: 12px; }
.dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 4px; }
.meta { font-size: 12px; color: #6b7787; margin-top: 3px; }
.dates { font-size: 12px; color: #8a97a8; margin-top: 2px; font-variant-numeric: tabular-nums; }
.dates .live { color: #e0392b; font-weight: 600; }
.sim { color: var(--accent); margin-left: 4px; }
.msg { padding: 16px; color: #6b7787; font-size: 13px; }
.msg.err { color: var(--danger); }
</style>
