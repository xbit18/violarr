<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { navigation } from '../composables/navigation'
import { createThemeController } from '../composables/theme'
import { useLocale, type Locale } from '../i18n'

const route = useRoute()
const theme = createThemeController()
const locale = useLocale()
const menuButton = ref<HTMLButtonElement | null>(null)
const sidebar = ref<HTMLElement | null>(null)
const main = ref<HTMLElement | null>(null)
const desktop = ref(true)
let media: MediaQueryList | null = null
function syncLayout() {
  desktop.value = media?.matches ?? true
}
function openNavigation() {
  navigation.toggle()
  if (navigation.mobileOpen.value)
    nextTick(() => sidebar.value?.querySelector<HTMLAnchorElement>('a')?.focus())
}
function closeNavigation() {
  navigation.close()
  menuButton.value?.focus()
}
function focusableLinks() {
  return [...(sidebar.value?.querySelectorAll<HTMLAnchorElement>('a') ?? [])]
}
function onKeydown(event: KeyboardEvent) {
  if (!navigation.mobileOpen.value) return
  if (event.key === 'Escape') {
    event.preventDefault()
    closeNavigation()
    return
  }
  if (event.key !== 'Tab') return
  const links = focusableLinks()
  if (!links.length) return
  if (!sidebar.value?.contains(document.activeElement)) {
    event.preventDefault()
    ;(event.shiftKey ? links[links.length - 1] : links[0]).focus()
  } else if (!event.shiftKey && document.activeElement === links[links.length - 1]) {
    event.preventDefault()
    links[0].focus()
  } else if (event.shiftKey && document.activeElement === links[0]) {
    event.preventDefault()
    links[links.length - 1].focus()
  }
}
watch(
  () => route.fullPath,
  async () => {
    const wasOpen = navigation.mobileOpen.value
    navigation.onRouteChange()
    if (wasOpen) {
      await nextTick()
      main.value?.focus()
    }
  },
)
onMounted(() => {
  media = window.matchMedia('(min-width: 801px)')
  syncLayout()
  media.addEventListener('change', syncLayout)
  document.addEventListener('keydown', onKeydown)
})
onBeforeUnmount(() => {
  media?.removeEventListener('change', syncLayout)
  document.removeEventListener('keydown', onKeydown)
  theme.dispose()
})
const links = [
  ['/', 'navigation.dashboard'],
  ['/settings', 'navigation.general'],
  ['/database', 'navigation.database'],
  ['/result-processing', 'navigation.results'],
  ['/prowlarr', 'Prowlarr'],
  ['/diagnostics', 'common.diagnostics'],
  ['/advanced', 'navigation.advanced'],
] as const
</script>
<template>
  <div class="app-shell">
    <header class="topbar">
      <button
        ref="menuButton"
        class="icon-button menu-button"
        type="button"
        :aria-label="locale.t('navigation.open')"
        :aria-expanded="navigation.mobileOpen.value"
        aria-controls="primary-navigation"
        @click="openNavigation"
      >
        ☰
      </button>
      <RouterLink
        class="brand"
        to="/"
        :aria-label="`${locale.t('app.title')} ${locale.t('navigation.dashboard')}`"
        ><img class="brand-logo" :src="'/logo.png'" alt="" /><span>Violarr</span></RouterLink
      >
      <label class="language-select"
        ><span class="sr-only">{{ locale.t('language.label') }}</span
        ><select
          :aria-label="locale.t('language.label')"
          :value="locale.current.value"
          @change="locale.setLocale(($event.target as HTMLSelectElement).value as Locale)"
        >
          <option value="it">{{ locale.t('language.it') }}</option>
          <option value="en">{{ locale.t('language.en') }}</option>
        </select></label
      >
      <label class="theme-select"
        ><span class="sr-only">{{ locale.t('theme.label') }}</span
        ><select
          :aria-label="locale.t('theme.label')"
          :value="theme.preference.value"
          @change="
            theme.setPreference(
              ($event.target as HTMLSelectElement).value as 'system' | 'light' | 'dark',
            )
          "
        >
          <option value="system">{{ locale.t('theme.system') }}</option>
          <option value="light">{{ locale.t('theme.light') }}</option>
          <option value="dark">{{ locale.t('theme.dark') }}</option>
        </select></label
      >
    </header>
    <div
      v-if="navigation.mobileOpen.value"
      class="scrim"
      aria-hidden="true"
      @click="closeNavigation"
    />
    <aside
      id="primary-navigation"
      ref="sidebar"
      class="sidebar"
      :class="{ 'sidebar--open': navigation.mobileOpen.value }"
      :aria-label="locale.t('navigation.primary')"
      :aria-hidden="!desktop && !navigation.mobileOpen.value"
      :inert="!desktop && !navigation.mobileOpen.value"
    >
      <nav>
        <RouterLink v-for="[path, label] in links" :key="path" :to="path">{{
          label === 'Prowlarr' ? label : locale.t(label)
        }}</RouterLink>
      </nav>
      <p class="sidebar-note">{{ locale.t('app.subtitle') }}</p>
    </aside>
    <main id="main-content" ref="main" class="main" tabindex="-1"><RouterView /></main>
  </div>
</template>
