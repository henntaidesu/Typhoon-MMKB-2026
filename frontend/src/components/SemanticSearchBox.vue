<template>
  <div class="sem">
    <div class="row">
      <input v-model="q" @keyup.enter="run"
             :placeholder="t('search.placeholder')" />
      <button @click="run" :disabled="store.loading">{{ t('search.button') }}</button>
    </div>
    <div class="hint">
      {{ t('search.hint') }}
      <a @click="quick('typhoon that caused severe flooding and landslides')">severe flooding</a> ·
      <a @click="quick('高潮・暴風による沿岸被害')">高潮被害</a>
    </div>
    <button v-if="store.semanticHits.size" class="clear" @click="reset">{{ t('search.clear') }}</button>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useTyphoonStore } from '../stores/typhoon'
const { t } = useI18n()
const store = useTyphoonStore()
const q = ref('')
function run() { if (q.value.trim()) store.semanticSearch(q.value.trim()) }
function quick(text) { q.value = text; run() }
function reset() { q.value = ''; store.loadList() }
</script>

<style scoped>
.sem { padding: 10px 12px; border-bottom: 1px solid #dde3ea; }
.row { display: flex; gap: 6px; }
input { flex: 1; padding: 7px 9px; border: 1px solid #c6cfda; border-radius: 6px; }
button { padding: 7px 12px; border: none; border-radius: 6px; background: var(--accent); color: #fff; }
button:disabled { opacity: .6; }
.hint { font-size: 11px; color: #6b7787; margin-top: 6px; line-height: 1.5; }
.hint a { color: var(--accent); cursor: pointer; text-decoration: underline; }
.clear { margin-top: 8px; background: #eef1f5; color: #33435a; width: 100%; }
</style>
