<template>
  <div class="layout">
    <aside class="sidebar">
      <SemanticSearchBox />
      <!-- While a search is active the sidebar shows its hits across all three
           knowledge layers; clearing it returns to the plain typhoon list. -->
      <SemanticResults v-if="store.search || store.loading" />
      <TyphoonList v-else />
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
</template>

<script setup>
import { useI18n } from 'vue-i18n'
import { useTyphoonStore } from '../stores/typhoon'
import MapView from '../components/MapView.vue'
import TyphoonList from '../components/TyphoonList.vue'
import SemanticSearchBox from '../components/SemanticSearchBox.vue'
import SemanticResults from '../components/SemanticResults.vue'
import TimelineSlider from '../components/TimelineSlider.vue'
import DetailPanel from '../components/DetailPanel.vue'

const { t } = useI18n()
const store = useTyphoonStore()
</script>

<style scoped>
.layout { display: flex; flex: 1; min-height: 0; height: 100%; }
.sidebar { width: var(--sidebar-w); background: #fff; display: flex; flex-direction: column; border-right: 1px solid #dde3ea; z-index: 600; }
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
