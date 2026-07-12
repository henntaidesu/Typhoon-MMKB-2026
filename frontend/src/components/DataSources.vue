<template>
  <div class="ds">
    <header class="ds-head">
      <div class="ds-head-row">
        <div></div>
        <button class="update-btn" :disabled="busy" @click="startUpdate">
          {{ updateState.status === 'running' ? '更新中…' : '更新进行中数据' }}
        </button>
      </div>
      <div v-if="updateState.message" class="update-msg" :class="{ err: updateState.status === 'error' }">
        {{ updateState.message }}
      </div>
    </header>

    <div v-if="loadErr" class="ds-err">加载失败：{{ loadErr }}</div>

    <div class="grid">
      <article
        v-for="s in sources"
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
            <input v-else v-model="form[s.key][p.name]" :placeholder="p.default || ''" />
          </label>
        </div>

        <div class="msg" v-if="s.state?.message">{{ s.state.message }}</div>

        <div class="counts" v-if="hasCounts(s)">
          <span v-for="(v, k) in s.state.counts" :key="k">{{ k }}: <b>{{ v }}</b></span>
        </div>

        <button
          class="go"
          :disabled="busy || s.state?.status === 'running'"
          @click="start(s)"
        >
          {{ s.state?.status === 'running' ? '爬取中…' : '开始爬取' }}
        </button>
      </article>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref, onMounted, onUnmounted, h } from 'vue'
import api from '../api/client'

const sources = ref([])
const form = reactive({})       // key -> { paramName: value }
const busy = ref(false)         // true while any crawl is active
const loadErr = ref(null)
const updateState = reactive({ status: 'idle', message: '' })  // the "更新进行中" action
let timer = null

// Small inline status badge component.
const StatusBadge = (props) => {
  const map = {
    running: ['运行中', '#0b6bcb'],
    done: ['完成', '#2e9e5b'],
    error: ['失败', '#d64545'],
    idle: ['待运行', '#98a4b3'],
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
    busy.value = !!data.active
    loadErr.value = null
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
    updateState.message = '启动中…'
    busy.value = true
  } catch (e) {
    alert(e.message || String(e))
  }
}

async function start(s) {
  const body = {}
  const f = form[s.key] || {}
  if ('variant' in f) body.variant = f.variant
  if ('years' in f) body.years = parseYears(f.years)
  try {
    const res = await api.startCrawl(s.key, body)
    s.state = { status: 'running', message: '启动中…', counts: {} }
    busy.value = true
  } catch (e) {
    alert(e.message || String(e))
  }
}

onMounted(async () => {
  await loadList()
  timer = setInterval(poll, 1500)
})
onUnmounted(() => clearInterval(timer))
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

.msg { font-size: 12px; color: #445167; background: #f5f8fc; border-radius: 6px; padding: 7px 9px; margin-bottom: 8px; word-break: break-word; }
.counts { display: flex; flex-wrap: wrap; gap: 10px; font-size: 12px; color: #445167; margin-bottom: 10px; }
.counts b { color: #0b2540; }

.go {
  margin-top: auto; border: none; background: #0b6bcb; color: #fff;
  padding: 9px 12px; border-radius: 8px; font-size: 14px; font-weight: 600;
}
.go:hover:not(:disabled) { background: #095aad; }
.go:disabled { background: #b7c3d2; cursor: not-allowed; }
</style>
