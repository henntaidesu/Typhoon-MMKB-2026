<template>
  <div class="list">
    <div class="filters">
      <input v-model.number="store.filters.year" type="number" placeholder="年份" @keyup.enter="store.loadList()" />
      <input v-model="store.filters.name" placeholder="名称" @keyup.enter="store.loadList()" />
      <button @click="store.loadList()">筛选</button>
    </div>
    <div v-if="store.loading" class="msg">加载中…</div>
    <div v-else-if="store.error" class="msg err">{{ store.error }}</div>
    <ul v-else>
      <li v-for="t in store.list" :key="t.id"
          :class="{ active: t.id === store.selectedId, hit: store.semanticHits.has(t.id) }"
          @click="store.select(t.id)">
        <div class="name">
          <span class="dot" :style="{ background: catColor(t.max_wind_kt) }"></span>
          {{ t.name || '(未命名)' }} <span class="id">#{{ t.intl_id }}</span>
        </div>
        <div class="meta">
          {{ t.season_year }} · {{ t.category || '—' }} · 峰值 {{ t.max_wind_kt ?? '?' }} kt
          <span v-if="t.distance != null" class="sim">语义距离 {{ t.distance }}</span>
        </div>
      </li>
    </ul>
    <div v-if="!store.loading && !store.list.length" class="msg">无数据（数据库未就绪或无匹配）</div>
  </div>
</template>

<script setup>
import { useTyphoonStore } from '../stores/typhoon'
const store = useTyphoonStore()
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
ul { list-style: none; margin: 0; padding: 0; }
li { padding: 9px 12px; border-bottom: 1px solid #eef1f5; cursor: pointer; }
li:hover { background: #f4f7fb; }
li.active { background: #e3effb; }
li.hit { border-left: 3px solid var(--accent); }
.name { font-weight: 600; font-size: 14px; }
.id { color: #98a4b3; font-weight: 400; font-size: 12px; }
.dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 4px; }
.meta { font-size: 12px; color: #6b7787; margin-top: 3px; }
.sim { color: var(--accent); margin-left: 4px; }
.msg { padding: 16px; color: #6b7787; font-size: 13px; }
.msg.err { color: var(--danger); }
</style>
