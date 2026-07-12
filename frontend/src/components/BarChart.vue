<template>
  <div class="bars">
    <div v-if="!items.length" class="empty">无数据</div>
    <div v-for="(it, i) in items" :key="i" class="row">
      <div class="lbl" :title="it.label">{{ it.label }}</div>
      <div class="track">
        <div class="fill" :style="{ width: pct(it.value) + '%', background: color }"></div>
      </div>
      <div class="val">{{ it.value }}</div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  items: { type: Array, default: () => [] },   // [{label, value}]
  color: { type: String, default: '#0b6bcb' },
})

const max = computed(() => Math.max(1, ...props.items.map((i) => i.value || 0)))
function pct(v) { return Math.round(((v || 0) / max.value) * 100) }
</script>

<style scoped>
.bars { display: flex; flex-direction: column; gap: 6px; }
.empty { color: #98a4b3; font-size: 13px; padding: 8px 0; }
.row { display: grid; grid-template-columns: 120px 1fr 44px; align-items: center; gap: 8px; }
.lbl { font-size: 12px; color: #445167; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.track { background: #eef2f7; border-radius: 4px; height: 16px; overflow: hidden; }
.fill { height: 100%; border-radius: 4px; transition: width .3s ease; min-width: 2px; }
.val { font-size: 12px; color: #0b2540; font-weight: 600; text-align: right; }
</style>
