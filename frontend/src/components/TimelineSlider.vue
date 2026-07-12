<template>
  <div v-if="points.length" class="timeline">
    <button @click="toggle" :title="playing ? $t('timeline.pause') : $t('timeline.play')">{{ playing ? '⏸' : '▶' }}</button>
    <input type="range" min="0" :max="points.length - 1" :value="store.timeIndex"
           @input="store.setTimeIndex(+$event.target.value)" />
    <div class="label">
      {{ current?.time?.slice(0, 16) || '' }}
      · {{ current?.wind_kt ?? '?' }} kt
      · {{ current?.pressure_hpa ?? '?' }} hPa
    </div>
  </div>
</template>

<script setup>
import { computed, onUnmounted, ref } from 'vue'
import { useTyphoonStore } from '../stores/typhoon'
const store = useTyphoonStore()
const points = computed(() => store.trackPoints)
const current = computed(() => points.value[store.timeIndex])
const playing = ref(false)
let timer = null

function toggle() {
  playing.value = !playing.value
  if (playing.value) {
    if (store.timeIndex >= points.value.length - 1) store.setTimeIndex(0)
    timer = setInterval(() => {
      if (store.timeIndex >= points.value.length - 1) { toggle(); return }
      store.setTimeIndex(store.timeIndex + 1)
    }, 250)
  } else {
    clearInterval(timer); timer = null
  }
}
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.timeline {
  position: absolute; left: 16px; right: 16px; bottom: 16px; z-index: 500;
  display: flex; align-items: center; gap: 12px;
  background: rgba(255,255,255,.94); padding: 10px 14px; border-radius: 10px;
  box-shadow: 0 2px 12px rgba(0,0,0,.15);
}
button { width: 34px; height: 34px; border: none; border-radius: 8px; background: var(--accent); color: #fff; font-size: 14px; }
input[type=range] { flex: 1; }
.label { font-size: 12px; color: #33435a; white-space: nowrap; min-width: 210px; }
</style>
