<template>
  <div class="results">
    <!-- A search replaces the sidebar wholesale, so showing the previous
         result set while the next one loads would read as a stale answer. -->
    <div v-if="store.loading" class="empty">{{ $t('list.loading') }}</div>
    <template v-else-if="r">
    <div class="summary">
      <span class="badge" :class="r.intent">{{ $t(`search.intent.${r.intent}`) }}</span>
      <span v-if="r.scoped" class="badge scoped">{{ $t('search.scopedBadge') }}</span>
      <span class="n">{{ $t('search.resultCount', { n: store.resultCount }) }}</span>
    </div>

    <p v-if="!store.resultCount" class="empty">
      {{ $t('search.noMatch', { d: r.max_distance }) }}
    </p>

    <!-- Exact-lookup answers (year / storm number / name) come first: they are
         facts, not rankings, so they carry no distance. -->
    <section v-if="r.structured.length">
      <h4>{{ $t('search.sectionExact') }}<span class="c">{{ r.structured.length }}</span></h4>
      <ul>
        <li v-for="t in r.structured" :key="'s' + t.id"
            :class="{ sel: t.id === store.selectedId }" @click="store.select(t.id)">
          <div class="title">{{ t.name || $t('list.unnamed') }} <span class="id">#{{ t.intl_id }}</span></div>
          <div class="meta">{{ t.season_year }} · {{ t.category || '—' }} · {{ t.max_wind_kt ?? '?' }} kt</div>
        </li>
      </ul>
    </section>

    <section v-if="r.typhoons.length">
      <h4>{{ $t('search.sectionTyphoons') }}<span class="c">{{ r.typhoons.length }}</span></h4>
      <ul>
        <li v-for="t in r.typhoons" :key="'t' + t.id"
            :class="{ sel: t.id === store.selectedId }" @click="store.select(t.id)">
          <div class="title">{{ t.name || $t('list.unnamed') }} <span class="id">#{{ t.intl_id }}</span></div>
          <div class="meta">{{ t.season_year }} · {{ t.category || '—' }}
            <span v-if="t.match === 'keyword'" class="kw">{{ $t('search.keywordHit') }}</span>
            <span class="score">{{ score(t.distance) }}</span></div>
        </li>
      </ul>
    </section>

    <section v-if="r.disasters.length">
      <h4>{{ $t('search.sectionDisasters') }}<span class="c">{{ r.disasters.length }}</span></h4>
      <ul>
        <li v-for="d in r.disasters" :key="'d' + d.id" @click="store.openHit(d)">
          <div class="title">
            <span class="tag dis">{{ d.disaster_type }}</span>
            {{ clip(d.description) }}
          </div>
          <div class="meta">
            <span v-if="d.region_name">{{ d.region_name }} · </span>{{ d.source }}
            <span v-if="d.lat != null" class="pin" :title="$t('search.locatable')">◉</span>
            <span v-if="d.match === 'keyword'" class="kw">{{ $t('search.keywordHit') }}</span>
            <span class="score">{{ score(d.distance) }}</span>
          </div>
        </li>
      </ul>
    </section>

    <section v-if="r.public_info.length">
      <h4>{{ $t('search.sectionPublic') }}<span class="c">{{ r.public_info.length }}</span></h4>
      <ul>
        <li v-for="p in r.public_info" :key="'p' + p.id" @click="store.openHit(p)">
          <div class="title">
            <span class="tag pub">{{ p.info_type }}</span>
            {{ clip(p.title || p.description) }}
          </div>
          <div class="meta">
            <span v-if="p.agency">{{ p.agency }}</span>
            <span v-if="p.severity"> · {{ p.severity }}</span>
            <span v-if="p.lat != null" class="pin" :title="$t('search.locatable')">◉</span>
            <span v-if="p.match === 'keyword'" class="kw">{{ $t('search.keywordHit') }}</span>
            <span class="score">{{ score(p.distance) }}</span>
          </div>
        </li>
      </ul>
    </section>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useTyphoonStore } from '../stores/typhoon'

const store = useTyphoonStore()
const r = computed(() => store.search)

function clip(s) {
  const text = (s || '').replace(/\s+/g, ' ').trim()
  return text.length > 90 ? text.slice(0, 90) + '…' : text
}

// Cosine distance is backwards (lower = better) and reads as an error figure,
// so show it as a 0-100 relevance score the user doesn't have to invert.
// Keyword and exact hits carry no distance — they aren't ranked, they matched.
function score(d) { return d == null ? '' : Math.round((1 - d) * 100) }
</script>

<style scoped>
.results { flex: 1; overflow-y: auto; }
.summary {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  padding: 8px 12px; border-bottom: 1px solid #eef1f5; font-size: 11px;
}
.badge { padding: 2px 6px; border-radius: 4px; background: #eef3f9; color: #46586f; font-weight: 600; }
.badge.year, .badge.intl_id, .badge.name { background: #e6f2e6; color: #2b6b32; }
.badge.scoped { background: #fdf0e3; color: #8a5216; }
.summary .n { margin-left: auto; color: #98a4b3; }
.empty { padding: 18px 14px; color: #6b7787; font-size: 12px; line-height: 1.6; }

h4 {
  margin: 0; padding: 7px 12px; font-size: 12px; color: #46586f;
  background: #eef3f9; border-bottom: 1px solid #e3e9f0;
  position: sticky; top: 0; z-index: 2; display: flex;
}
h4 .c { margin-left: auto; color: #98a4b3; }
ul { list-style: none; margin: 0; padding: 0; }
li { padding: 8px 12px; border-bottom: 1px solid #f1f4f8; cursor: pointer; }
li:hover { background: #f4f7fb; }
li.sel { background: #e3effb; }
.title { font-size: 13px; color: #26313f; line-height: 1.45; }
.id { color: #98a4b3; font-weight: 400; font-size: 12px; }
.meta { font-size: 11px; color: #8a97a8; margin-top: 3px; display: flex; align-items: center; gap: 2px; }
.meta .kw {
  margin-left: auto; padding: 0 4px; border-radius: 3px;
  background: #e6f2e6; color: #2b6b32; font-weight: 700; font-size: 10px;
}
.meta .kw + .score { margin-left: 6px; }
.meta .score { margin-left: auto; color: var(--accent); font-weight: 700; font-variant-numeric: tabular-nums; }
.pin { color: var(--accent); margin-left: 4px; }
.tag {
  display: inline-block; padding: 1px 5px; border-radius: 3px;
  font-size: 10px; font-weight: 700; margin-right: 5px; vertical-align: 1px;
}
.tag.dis { background: #fdecea; color: #a5342a; }
.tag.pub { background: #e8f0fb; color: #24558f; }
</style>
