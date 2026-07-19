<template>
  <div class="app-shell">
    <nav class="topnav">
      <div class="nav-brand">{{ t('nav.brand') }}</div>
      <router-link to="/map" class="navlink" active-class="active">{{ t('nav.map') }}</router-link>
      <router-link to="/stats" class="navlink" active-class="active">{{ t('nav.stats') }}</router-link>
      <router-link to="/sources" class="navlink" active-class="active">{{ t('nav.sources') }}</router-link>
      <select class="lang-select" :value="locale" @change="setLocale($event.target.value)">
        <option v-for="l in SUPPORTED" :key="l" :value="l">{{ messages[l].langName }}</option>
      </select>
    </nav>

    <router-view class="view" />
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { SUPPORTED, setLocale } from './i18n'
import L from 'leaflet'
import markerIcon from 'leaflet/dist/images/marker-icon.png'
import markerIcon2x from 'leaflet/dist/images/marker-icon-2x.png'
import markerShadow from 'leaflet/dist/images/marker-shadow.png'
import { useTyphoonStore } from './stores/typhoon'

const { t, locale, messages } = useI18n()

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
.topnav .navlink {
  background: transparent; border: none; color: #b9c6d6; font-size: 14px;
  padding: 6px 14px; border-radius: 6px; text-decoration: none; cursor: pointer;
}
.topnav .navlink:hover { color: #fff; background: rgba(255, 255, 255, .08); }
.topnav .navlink.active { color: #fff; background: rgba(255, 255, 255, .16); }
.lang-select {
  margin-left: auto; background: rgba(255, 255, 255, .1); color: #fff;
  border: 1px solid rgba(255, 255, 255, .2); border-radius: 6px;
  padding: 5px 8px; font-size: 13px; cursor: pointer;
}
.lang-select option { color: #1a2233; }
.view { flex: 1; min-height: 0; background: #eef1f5; }
</style>
