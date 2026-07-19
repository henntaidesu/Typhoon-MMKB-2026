import { createRouter, createWebHistory } from 'vue-router'
import MapPage from '../views/MapPage.vue'
import StatsView from '../components/StatsView.vue'
import DataSources from '../components/DataSources.vue'

const routes = [
  { path: '/', redirect: '/map' },
  { path: '/map', name: 'map', component: MapPage },
  { path: '/stats', name: 'stats', component: StatsView },
  { path: '/sources', name: 'sources', component: DataSources },
  // Unknown paths fall back to the map instead of a blank page.
  { path: '/:pathMatch(.*)*', redirect: '/map' },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})
