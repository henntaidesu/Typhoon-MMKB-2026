<template>
  <div class="sem">
    <div class="row">
      <input v-model="q" @keyup.enter="run"
             :placeholder="t('search.placeholder')" />
      <button @click="run" :disabled="store.loading">{{ t('search.button') }}</button>
    </div>

    <!-- Spatio-temporal scope: turns the semantic ranking into a 時空間 x 意味 join -->
    <div class="scope">
      <label class="chk">
        <input type="checkbox" v-model="store.scope.useBbox" />
        <span>{{ t('search.scopeBbox') }}</span>
      </label>
      <div class="years">
        <input type="number" v-model.number="store.scope.yearFrom"
               :placeholder="t('search.yearFrom')" />
        <span class="dash">–</span>
        <input type="number" v-model.number="store.scope.yearTo"
               :placeholder="t('search.yearTo')" />
      </div>
    </div>

    <div class="hint">
      {{ t('search.hint') }}
      <a v-for="ex in examples" :key="ex" @click="quick(ex)">{{ ex }}</a>
    </div>
    <button v-if="store.search" class="clear" @click="reset">{{ t('search.clear') }}</button>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useTyphoonStore } from '../stores/typhoon'
const { t, tm, rt } = useI18n()
const store = useTyphoonStore()
const q = ref('')
// Locale-specific examples: a query only ranks well against text the KB
// actually holds in that language, so each locale suggests its own. tm() hands
// back compiled messages, hence rt() to get plain strings.
const examples = computed(() =>
  (tm('search.examples') || []).map((m) => (typeof m === 'string' ? m : rt(m))))
function run() { if (q.value.trim()) store.semanticSearch(q.value.trim()) }
function quick(text) { q.value = text; run() }
function reset() { q.value = ''; store.clearSearch() }
</script>

<style scoped>
.sem { padding: 10px 12px; border-bottom: 1px solid #dde3ea; }
.row { display: flex; gap: 6px; }
input { flex: 1; padding: 7px 9px; border: 1px solid #c6cfda; border-radius: 6px; }
button { padding: 7px 12px; border: none; border-radius: 6px; background: var(--accent); color: #fff; }
button:disabled { opacity: .6; }

.scope { display: flex; align-items: center; gap: 10px; margin-top: 8px; font-size: 11px; color: #6b7787; }
.chk { display: flex; align-items: center; gap: 4px; cursor: pointer; }
.chk input { flex: none; }
.years { display: flex; align-items: center; gap: 4px; margin-left: auto; }
.years input { width: 52px; padding: 3px 5px; font-size: 11px; }
.dash { color: #98a4b3; }

.hint { font-size: 11px; color: #6b7787; margin-top: 6px; line-height: 1.7; }
.hint a { color: var(--accent); cursor: pointer; text-decoration: underline; margin-right: 8px; }
.clear { margin-top: 8px; background: #eef1f5; color: #33435a; width: 100%; }
</style>
