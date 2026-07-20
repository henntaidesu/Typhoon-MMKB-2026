<template>
  <div class="ds">
    <header class="ds-head">
      <div class="ds-head-row">
        <div></div>
        <button class="update-btn" :disabled="busy" @click="startUpdate">
          {{ updateState.status === 'running' ? t('sources.updating') : t('sources.updateData') }}
        </button>
      </div>
      <div v-if="updateState.message" class="update-msg" :class="{ err: updateState.status === 'error' }">
        {{ updateState.message }}
      </div>
    </header>

    <div v-if="loadErr" class="ds-err">{{ t('sources.loadFailed') }}{{ loadErr }}</div>

    <section v-for="g in groups" :key="g.key" class="cat">
      <div class="cat-head">
        <h3 class="cat-title">{{ g.label }}</h3>
        <span v-if="g.desc" class="cat-desc">{{ g.desc }}</span>
      </div>
      <div class="grid">
      <article
        v-for="s in g.items"
        :key="s.key"
        class="card"
        :class="{ running: s.state?.status === 'running' }"
      >
        <div class="card-top">
          <span class="kind">{{ s.kind }}</span>
          <StatusBadge :status="s.state?.status" />
        </div>

        <h3>{{ s.name }}</h3>
        <div class="provider">{{ s.provider }}</div>

        <div v-if="s.params.length" class="params">
          <label v-for="p in s.params" :key="p.name" class="param">
            <span>{{ p.label }}</span>
            <select v-if="p.type === 'select'" v-model="form[s.key][p.name]">
              <option v-for="o in p.options" :key="o" :value="o">{{ o }}</option>
            </select>
            <template v-else-if="p.type === 'typhoon'">
              <div class="typ-cascade">
                <select
                  :value="typYear[s.key + '.' + p.name] || ''"
                  @change="onYearChange(s.key, p.name, $event.target.value)"
                >
                  <option value="">{{ t('sources.selectYear') }}</option>
                  <option v-for="y in typhoonYears" :key="y" :value="y">{{ y }}</option>
                </select>
                <select
                  v-model="form[s.key][p.name]"
                  :disabled="!typYear[s.key + '.' + p.name]"
                >
                  <option value="">{{ t('sources.selectTyphoon') }}</option>
                  <option
                    v-for="ty in typhoonsForYear(typYear[s.key + '.' + p.name])"
                    :key="ty.intl_id"
                    :value="ty.intl_id"
                  >
                    {{ ty.intl_id }} · {{ ty.name || '—' }}
                  </option>
                </select>
              </div>
            </template>
            <input v-else v-model="form[s.key][p.name]" :placeholder="p.default || ''" />
          </label>
        </div>

        <div class="msg" v-if="s.state?.message">{{ s.state.message }}</div>

        <div class="counts" v-if="hasCounts(s)">
          <span v-for="(v, k) in s.state.counts" :key="k">{{ k }}: <b>{{ v }}</b></span>
        </div>

        <div v-if="s.temporal" class="actions">
          <button
            class="go new"
            :disabled="busy || s.state?.status === 'running'"
            @click="start(s, 'new')"
          >
            {{ s.state?.status === 'running' ? t('sources.crawling') : t('sources.fetchNew') }}
          </button>
          <button
            class="go hist"
            :disabled="busy || s.state?.status === 'running'"
            @click="start(s, 'history')"
          >
            {{ s.state?.status === 'running' ? t('sources.crawling') : t('sources.fetchHistory') }}
          </button>
        </div>
        <button
          v-else
          class="go"
          :disabled="busy || s.state?.status === 'running'"
          @click="start(s)"
        >
          {{ s.state?.status === 'running' ? t('sources.crawling') : t('sources.startCrawl') }}
        </button>
      </article>
      </div>
    </section>
  </div>
</template>

<script setup>
import { reactive, ref, computed, onMounted, onUnmounted, h } from 'vue'
import { useI18n } from 'vue-i18n'
import api from '../api/client'

const { t } = useI18n()

const sources = ref([])
const categories = ref([])
const typhoons = ref([])   // for 'typhoon'-type params (e.g. GDELT news search)
// Selected year per typhoon-picker, keyed by "<sourceKey>.<paramName>". Drives the
// cascading year → typhoon selects so the typhoon list stays scoped to one season.
const typYear = reactive({})

// Distinct season years present in the typhoon list, newest first.
const typhoonYears = computed(() => {
  const ys = new Set()
  for (const ty of typhoons.value) if (ty.season_year != null) ys.add(ty.season_year)
  return Array.from(ys).sort((a, b) => b - a)
})

// Typhoons within one season, sorted by intl_id so the picker reads in order.
function typhoonsForYear(year) {
  if (!year) return []
  const y = Number(year)
  return typhoons.value
    .filter((ty) => ty.season_year === y)
    .sort((a, b) => String(a.intl_id).localeCompare(String(b.intl_id)))
}

// Switching year clears the previously picked typhoon (it belongs to another season).
function onYearChange(sourceKey, paramName, year) {
  typYear[sourceKey + '.' + paramName] = year
  if (form[sourceKey]) form[sourceKey][paramName] = ''
}

// Group the flat source list into ordered category sections so related feeds
// (台风路径 / 受灾情报 / 公共情报 / 地理边界 / 多媒体) sit together instead of
// one undifferentiated grid. Falls back to a single "其他" group for any source
// whose category isn't in the catalogue.
const groups = computed(() => {
  const cats = categories.value.length
    ? categories.value
    : [{ key: '', label: '', desc: '' }]
  const out = cats.map((c) => ({ ...c, items: [] }))
  const byKey = new Map(out.map((g) => [g.key, g]))
  let other = null
  for (const s of sources.value) {
    const g = byKey.get(s.category || '')
    if (g) {
      g.items.push(s)
    } else {
      if (!other) { other = { key: '__other', label: t('sources.otherCategory'), desc: '', items: [] }; out.push(other) }
      other.items.push(s)
    }
  }
  return out.filter((g) => g.items.length)
})
const form = reactive({})       // key -> { paramName: value }
const busy = ref(false)         // true while any crawl is active
const loadErr = ref(null)
const updateState = reactive({ status: 'idle', message: '' })  // the "更新进行中" action
let timer = null

// Small inline status badge component.
const StatusBadge = (props) => {
  const map = {
    running: [t('sources.statusRunning'), '#0b6bcb'],
    done: [t('sources.statusDone'), '#2e9e5b'],
    error: [t('sources.statusError'), '#d64545'],
    idle: [t('sources.statusIdle'), '#98a4b3'],
  }
  const [txt, color] = map[props.status] || map.idle
  return h('span', { class: 'badge', style: { background: color } }, txt)
}
StatusBadge.props = ['status']

function depName(key) {
  return sources.value.find((s) => s.key === key)?.name || key
}
function hasCounts(s) {
  return s.state?.counts && Object.keys(s.state.counts).length > 0
}

function ensureForm(list) {
  for (const s of list) {
    if (!form[s.key]) {
      form[s.key] = {}
      for (const p of s.params) form[s.key][p.name] = p.default ?? ''
    }
  }
}

function parseYears(v) {
  if (!v || !String(v).trim()) return null
  const out = []
  for (const tok of String(v).split(/[,，\s]+/).filter(Boolean)) {
    const range = tok.match(/^(\d{4})\s*[-—~]\s*(\d{4})$/)
    if (range) {
      let [a, b] = [parseInt(range[1], 10), parseInt(range[2], 10)]
      if (a > b) [a, b] = [b, a]
      for (let y = a; y <= b; y++) out.push(y)
    } else {
      const n = parseInt(tok, 10)
      if (!Number.isNaN(n)) out.push(n)
    }
  }
  return out.length ? Array.from(new Set(out)) : null
}

async function loadList() {
  try {
    const data = await api.listSources()
    ensureForm(data.sources)
    sources.value = data.sources
    categories.value = data.categories || []
    busy.value = !!data.active
    loadErr.value = null
    // If any source needs a typhoon picker, load the list once (newest first).
    if (!typhoons.value.length &&
        data.sources.some((s) => s.params.some((p) => p.type === 'typhoon'))) {
      try {
        typhoons.value = await api.listTyphoons({ limit: 20000 })
      } catch { /* selector just stays empty */ }
    }
  } catch (e) {
    loadErr.value = String(e.message || e)
  }
}

// Merge lightweight status into the existing cards (keeps form inputs intact).
async function poll() {
  try {
    const { active, status } = await api.sourcesStatus()
    busy.value = !!active
    for (const s of sources.value) {
      const st = status[s.key]
      if (st) s.state = { status: st.status, message: st.message, counts: st.counts,
                          started_at: st.started_at, finished_at: st.finished_at }
    }
    const u = status.update
    if (u) { updateState.status = u.status; updateState.message = u.message }
  } catch { /* transient poll error — ignore */ }
}

async function startUpdate() {
  try {
    await api.startCrawl('update', {})
    updateState.status = 'running'
    updateState.message = t('sources.starting')
    busy.value = true
    pollSoon()
  } catch (e) {
    alert(e.message || String(e))
  }
}

async function start(s, mode) {
  const body = {}
  const f = form[s.key] || {}
  if ('variant' in f) body.variant = f.variant
  if ('years' in f) body.years = parseYears(f.years)
  if ('maxrecords' in f) body.maxrecords = f.maxrecords
  if ('intl_id' in f) {
    // The datalist input may hold "2609" or a pasted "2609 · Bavi (2026)" — keep
    // the leading token, which is the intl_id the backend matches on.
    const iid = String(f.intl_id || '').trim().split(/[\s·]/)[0]
    if (!iid) { alert(t('sources.pickTyphoon')); return }
    body.intl_id = iid
  }
  if (mode) body.mode = mode      // 'new' | 'history' for temporal sources
  try {
    await api.startCrawl(s.key, body)
    s.state = { status: 'running', message: t('sources.starting'), counts: {} }
    busy.value = true
    pollSoon()
  } catch (e) {
    alert(e.message || String(e))
  }
}

// Self-scheduling rather than setInterval, for two reasons.
//
// Overlap: a crawl holds the backend busy (the embedding model runs in-process),
// so a status request can take longer than the interval. setInterval would keep
// firing regardless, letting responses arrive out of order and a stale one push
// a card from "done" back to "running".
//
// Rate: this page is idle most of the time — crawls are started by hand — so a
// 1.5s heartbeat only earns its keep while something is actually running.
const POLL_BUSY_MS = 1500
const POLL_IDLE_MS = 10000
let stopped = false

function schedulePoll(delay = busy.value ? POLL_BUSY_MS : POLL_IDLE_MS) {
  if (stopped) return
  clearTimeout(timer)
  timer = setTimeout(async () => {
    await poll()
    schedulePoll()
  }, delay)
}

// Just-started crawl: the next tick may be sitting on the idle delay, so pull it
// forward — otherwise the card would show nothing for ten seconds.
function pollSoon() { schedulePoll(300) }

onMounted(async () => {
  await loadList()
  schedulePoll()
})
onUnmounted(() => { stopped = true; clearTimeout(timer) })
</script>

<style scoped>
.ds { height: 100%; overflow-y: auto; padding: 22px 26px; }
.ds-head-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.ds-head h2 { margin: 0 0 4px; font-size: 20px; }
.ds-head p { margin: 0 0 18px; color: #6b7787; font-size: 13px; }
.update-btn {
  flex: 0 0 auto; border: 1px solid #0b6bcb; background: #eaf3fc; color: #0b6bcb;
  padding: 8px 14px; border-radius: 8px; font-size: 13px; font-weight: 600; white-space: nowrap;
}
.update-btn:hover:not(:disabled) { background: #0b6bcb; color: #fff; }
.update-btn:disabled { border-color: #c3cedb; color: #a2adbb; background: #f1f4f8; cursor: not-allowed; }
.update-msg { font-size: 12px; color: #445167; background: #f5f8fc; border-radius: 6px; padding: 7px 9px; margin-bottom: 14px; }
.update-msg.err { color: #b93b3b; background: #fdecec; }
.ds-err { background: #fdecec; color: #b93b3b; padding: 10px 12px; border-radius: 8px; margin-bottom: 14px; font-size: 13px; }

.cat { margin-bottom: 26px; }
.cat-head { display: flex; align-items: baseline; gap: 10px; margin: 0 0 12px; padding-bottom: 6px; border-bottom: 1px solid #e6ebf2; }
.cat-title { margin: 0; font-size: 15px; font-weight: 700; color: #16233a; }
.cat-desc { font-size: 12px; color: #8a97a8; }

.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px; }

.card {
  background: #fff; border: 1px solid #e3e8ef; border-radius: 12px;
  padding: 16px; display: flex; flex-direction: column;
  box-shadow: 0 1px 3px rgba(16, 34, 60, .05); transition: box-shadow .15s, border-color .15s;
}
.card:hover { box-shadow: 0 4px 16px rgba(16, 34, 60, .1); }
.card.running { border-color: #7fb4e8; box-shadow: 0 0 0 3px rgba(11, 107, 203, .12); }

.card-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.kind { font-size: 11px; color: #0b6bcb; background: #eaf3fc; padding: 2px 8px; border-radius: 999px; }
:deep(.badge) { color: #fff; font-size: 11px; padding: 2px 8px; border-radius: 999px; }

.card h3 { margin: 0; font-size: 16px; }
.provider { color: #98a4b3; font-size: 12px; margin: 2px 0 8px; }
.depends { font-size: 12px; color: #c07a15; background: #fff6e6; padding: 4px 8px; border-radius: 6px; margin-bottom: 8px; }

.params { display: flex; flex-direction: column; gap: 8px; margin-bottom: 10px; }
.param { display: flex; flex-direction: column; gap: 3px; font-size: 12px; color: #6b7787; }
.param select, .param input {
  padding: 6px 8px; border: 1px solid #d3dae4; border-radius: 6px; font-size: 13px; color: #1a2233;
}
.typ-cascade { display: flex; gap: 6px; }
.typ-cascade select { flex: 1; min-width: 0; }
.typ-cascade select:first-child { flex: 0 0 90px; }
.typ-cascade select:disabled { background: #f1f4f8; color: #a2adbb; cursor: not-allowed; }

.msg { font-size: 12px; color: #445167; background: #f5f8fc; border-radius: 6px; padding: 7px 9px; margin-bottom: 8px; word-break: break-word; }
.counts { display: flex; flex-wrap: wrap; gap: 10px; font-size: 12px; color: #445167; margin-bottom: 10px; }
.counts b { color: #0b2540; }

.go {
  margin-top: auto; border: none; background: #0b6bcb; color: #fff;
  padding: 9px 12px; border-radius: 8px; font-size: 14px; font-weight: 600;
}
.go:hover:not(:disabled) { background: #095aad; }
.go:disabled { background: #b7c3d2; cursor: not-allowed; }

.actions { margin-top: auto; display: flex; gap: 8px; }
.actions .go { margin-top: 0; flex: 1; }
/* New (current-season) is the primary action; history is the secondary backfill. */
.actions .go.hist { background: #eef2f7; color: #45536a; border: 1px solid #d3dae4; }
.actions .go.hist:hover:not(:disabled) { background: #e2e8f0; color: #2b3648; }
.actions .go.hist:disabled { background: #f1f4f8; color: #a2adbb; }
</style>
