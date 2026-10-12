import { createRouter, createWebHistory } from 'vue-router'
import DashboardView from './views/DashboardView.vue'
import GeneralView from './views/GeneralView.vue'
import DatabaseUpdatesView from './views/DatabaseUpdatesView.vue'
import ResultProcessingView from './views/ResultProcessingView.vue'
import ProwlarrView from './views/ProwlarrView.vue'
import AdvancedView from './views/AdvancedView.vue'
import DiagnosticsView from './views/DiagnosticsView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: DashboardView },
    { path: '/settings', component: GeneralView },
    { path: '/database', component: DatabaseUpdatesView },
    { path: '/result-processing', component: ResultProcessingView },
    { path: '/prowlarr', component: ProwlarrView },
    { path: '/advanced', component: AdvancedView },
    { path: '/diagnostics', component: DiagnosticsView },
  ],
})
