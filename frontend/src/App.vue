<template>
  <div class="app-shell">
    <nav class="topnav">
      <div class="nav-brand">{{ t('nav.brand') }}</div>
      <button :class="{ active: view === 'map' }" @click="view = 'map'">{{ t('nav.map') }}</button>
      <button :class="{ active: view === 'stats' }" @click="view = 'stats'">{{ t('nav.stats') }}</button>
      <button :class="{ active: view === 'sources' }" @click="view = 'sources'">{{ t('nav.sources') }}</button>
      <select class="lang-select" :value="locale" @change="setLocale($event.target.value)">
        <option v-for="l in SUPPORTED" :key="l" :value="l">{{ messages[l].langName }}</option>
      </select>
    </nav>

    <DataSources v-show="view === 'sources'" class="view" />
    <StatsView v-if="view === 'stats'" class="view" />

    <div class="layout" v-show="view === 'map'">
    <aside class="sidebar">
      <SemanticSearchBox />
      <TyphoonList />
    </aside>

    <main class="stage">
      <MapView />
      <TimelineSlider />
      <div class="legend-card">
        <div><span class="sw" style="background:#c0392b"></span>{{ t('legend.cat45') }}</div>
        <div><span class="sw" style="background:#e67e22"></span>{{ t('legend.cat3') }}</div>
        <div><span class="sw" style="background:#f39c12"></span>{{ t('legend.cat12') }}</div>
        <div><span class="sw" style="background:#2ecc71"></span>{{ t('legend.ts') }}</div>
        <div><span class="sw" style="background:#3498db"></span>{{ t('legend.td') }}</div>
      </div>
    </main>

    <transition name="slide">
      <section v-if="store.selected" class="panel">
        <DetailPanel />
      </section>
    </transition>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { SUPPORTED, setLocale } from './i18n'
import L from 'leaflet'
import markerIcon from 'leaflet/dist/images/marker-icon.png'
import markerIcon2x from 'leaflet/dist/images/marker-icon-2x.png'
import markerShadow from 'leaflet/dist/images/marker-shadow.png'
import { useTyphoonStore } from './stores/typhoon'
import MapView from './components/MapView.vue'
import TyphoonList from './components/TyphoonList.vue'
import SemanticSearchBox from './components/SemanticSearchBox.vue'
import TimelineSlider from './components/TimelineSlider.vue'
import DetailPanel from './components/DetailPanel.vue'
import DataSources from './components/DataSources.vue'
import StatsView from './components/StatsView.vue'

const { t, locale, messages } = useI18n()
const view = ref('map')  // 'map' | 'stats' | 'sources'

// Fix Leaflet's default marker icon paths under Vite bundling.
L.Icon.Default.mergeOptions({
  iconUrl: markerIcon, iconRetinaUrl: markerIcon2x, shadowUrl: markerShadow,
})

const store = useTyphoonStore()
onMounted(() => store.loadList())
</script>

<style scoped>
.app-shell { display: flex; flex-direction: column; height: 100%; }
.topnav {
  display: flex; align-items: center; gap: 6px; height: 46px; flex: 0 0 46px;
  background: #0b2540; padding: 0 14px; z-index: 700;
}
.nav-brand { color: #fff; font-weight: 700; font-size: 15px; margin-right: 14px; }
.topnav button {
  background: transparent; border: none; color: #b9c6d6; font-size: 14px;
  padding: 6px 14px; border-radius: 6px;
}
.topnav button:hover { color: #fff; background: rgba(255, 255, 255, .08); }
.topnav button.active { color: #fff; background: rgba(255, 255, 255, .16); }
.lang-select {
  margin-left: auto; background: rgba(255, 255, 255, .1); color: #fff;
  border: 1px solid rgba(255, 255, 255, .2); border-radius: 6px;
  padding: 5px 8px; font-size: 13px; cursor: pointer;
}
.lang-select option { color: #1a2233; }
.view { flex: 1; min-height: 0; background: #eef1f5; }
.layout { display: flex; flex: 1; min-height: 0; }
.sidebar { width: var(--sidebar-w); background: #fff; display: flex; flex-direction: column; border-right: 1px solid #dde3ea; z-index: 600; }
.brand { padding: 14px 12px; background: #0b2540; color: #fff; }
.title { font-size: 16px; font-weight: 700; }
.sub { font-size: 11px; opacity: .8; margin-top: 2px; }
.stage { position: relative; flex: 1; }
.panel { width: 340px; background: #fff; border-left: 1px solid #dde3ea; z-index: 600; }
.legend-card {
  position: absolute; right: 12px; top: 12px; z-index: 500;
  background: rgba(255,255,255,.94); padding: 8px 10px; border-radius: 8px;
  font-size: 11px; box-shadow: 0 1px 6px rgba(0,0,0,.12);
}
.legend-card div { display: flex; align-items: center; gap: 5px; }
.sw { width: 11px; height: 11px; border-radius: 2px; display: inline-block; }
.slide-enter-active, .slide-leave-active { transition: transform .2s ease; }
.slide-enter-from, .slide-leave-to { transform: translateX(100%); }
</style>
