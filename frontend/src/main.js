import { createApp } from 'vue'
import { createPinia } from 'pinia'
import 'leaflet/dist/leaflet.css'
import './style.css'
import App from './App.vue'
import { i18n } from './i18n'
import { router } from './router'

createApp(App).use(createPinia()).use(i18n).use(router).mount('#app')
