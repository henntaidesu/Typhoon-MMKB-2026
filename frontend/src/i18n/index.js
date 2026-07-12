import { createI18n } from 'vue-i18n'
import zh from './locales/zh.js'
import ja from './locales/ja.js'
import en from './locales/en.js'

export const SUPPORTED = ['zh', 'ja', 'en']
const STORAGE_KEY = 'mmkb-locale'

// Resolve the initial locale: saved choice → browser language → 'zh'.
function detectLocale() {
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved && SUPPORTED.includes(saved)) return saved
  const nav = (navigator.language || 'zh').toLowerCase()
  if (nav.startsWith('ja')) return 'ja'
  if (nav.startsWith('en')) return 'en'
  if (nav.startsWith('zh')) return 'zh'
  return 'zh'
}

export const i18n = createI18n({
  legacy: false,          // Composition API mode
  globalInjection: true,  // enable $t in templates
  locale: detectLocale(),
  fallbackLocale: 'en',
  messages: { zh, ja, en },
})

// Switch the active language and persist the choice.
export function setLocale(locale) {
  if (!SUPPORTED.includes(locale)) return
  i18n.global.locale.value = locale
  localStorage.setItem(STORAGE_KEY, locale)
  document.documentElement.setAttribute('lang', locale)
}

document.documentElement.setAttribute('lang', i18n.global.locale.value)
