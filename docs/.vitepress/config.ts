import { defineConfig } from 'vitepress'

const italianSidebar = [
  {
    text: 'Per iniziare',
    items: [
      { text: 'Installazione', link: '/getting-started/installation' },
      { text: 'Prima configurazione', link: '/getting-started/first-setup' },
      { text: 'Collegare Prowlarr', link: '/configuration/prowlarr' },
    ],
  },
  {
    text: 'Usare Violarr',
    items: [
      { text: 'Panoramica WebUI', link: '/configuration/webui' },
      { text: 'Diagnostica', link: '/features/diagnostics' },
      {
        text: 'Aggiornamenti database',
        link: '/configuration/database-updates',
      },
      {
        text: 'Elaborazione risultati',
        link: '/configuration/result-processing',
      },
      { text: 'Regole personalizzate', link: '/features/custom-filters' },
    ],
  },
  {
    text: 'Gestione e recupero',
    items: [
      { text: 'Aggiornare Violarr', link: '/getting-started/docker' },
      { text: 'Docker Compose', link: '/getting-started/docker-compose' },
      { text: 'Risoluzione dei problemi', link: '/troubleshooting' },
    ],
  },
  {
    text: 'Avanzato e sviluppo',
    items: [
      { text: 'Architettura', link: '/how-it-works/architecture' },
      { text: 'PostgreSQL', link: '/how-it-works/postgresql' },
      { text: 'Snapshot', link: '/how-it-works/snapshots' },
      { text: 'Aggiornamenti sicuri', link: '/how-it-works/safe-updates' },
      { text: "Variabili d'ambiente", link: '/configuration/environment' },
      { text: 'API', link: '/reference/api' },
      { text: 'Funzionalità Torznab', link: '/reference/torznab-capabilities' },
      {
        text: 'Schema di configurazione',
        link: '/reference/configuration-schema',
      },
      {
        text: 'Contribuire',
        link: 'https://github.com/xbit18/violarr/blob/develop/CONTRIBUTING.md',
      },
      { text: 'Changelog', link: '/changelog' },
    ],
  },
]

const englishLabels: Record<string, string> = {
  'Per iniziare': 'Getting started',
  'Usare Violarr': 'Using Violarr',
  'Gestione e recupero': 'Operations and recovery',
  'Avanzato e sviluppo': 'Advanced and development',
  Installazione: 'Installation',
  'Prima configurazione': 'First setup',
  'Collegare Prowlarr': 'Connect Prowlarr',
  'Panoramica WebUI': 'WebUI overview',
  Diagnostica: 'Diagnostics',
  'Aggiornamenti database': 'Database updates',
  'Elaborazione risultati': 'Result processing',
  'Regole personalizzate': 'Custom rules',
  'Aggiornare Violarr': 'Update Violarr',
  'Risoluzione dei problemi': 'Troubleshooting',
  "Variabili d'ambiente": 'Environment variables',
  Architettura: 'Architecture',
  'Aggiornamenti sicuri': 'Safe updates',
  'Funzionalità Torznab': 'Torznab capabilities',
  'Schema di configurazione': 'Configuration schema',
  Contribuire: 'Contributing guide',
}

const englishSidebar = italianSidebar.map((section) => ({
  ...section,
  text: englishLabels[section.text] ?? section.text,
  items: section.items.map((item) => ({
    ...item,
    text: englishLabels[item.text] ?? item.text,
    link: item.link.startsWith('http') ? item.link : `/en${item.link}`,
  })),
}))

export default defineConfig({
  title: 'Violarr',
  description: 'L’integrazione Prowlarr per Il Corsaro Viola',
  base: '/violarr/',
  cleanUrls: true,
  lastUpdated: true,
  head: [
    ['link', { rel: 'icon', type: 'image/png', href: '/violarr/favicon.png' }],
    ['meta', { name: 'theme-color', content: '#7c3aed' }],
  ],
  locales: {
    root: {
      label: 'Italiano',
      lang: 'it-IT',
      markdown: {
        container: {
          infoLabel: 'Informazione',
          tipLabel: 'Suggerimento',
          warningLabel: 'Attenzione',
          dangerLabel: 'Pericolo',
          detailsLabel: 'Dettagli',
        },
        codeCopyButton: {
          tooltipText: 'Copia codice',
          copiedText: 'Codice copiato',
        },
      },
      themeConfig: {
        siteTitle: 'Violarr',
        nav: [
          { text: 'Per iniziare', link: '/getting-started/installation' },
          { text: 'Usare Violarr', link: '/configuration/webui' },
          { text: 'Gestione e recupero', link: '/getting-started/docker' },
          { text: 'Avanzato e sviluppo', link: '/how-it-works/architecture' },
        ],
        sidebar: italianSidebar,
        outline: { label: 'In questa pagina' },
        docFooter: { prev: 'Pagina precedente', next: 'Pagina successiva' },
        lastUpdated: { text: 'Ultimo aggiornamento' },
        darkModeSwitchLabel: 'Aspetto',
        lightModeSwitchTitle: 'Passa al tema chiaro',
        darkModeSwitchTitle: 'Passa al tema scuro',
        sidebarMenuLabel: 'Menu',
        returnToTopLabel: 'Torna in cima',
        langMenuLabel: 'Cambia lingua',
        skipToContentLabel: 'Vai al contenuto',
        notFound: {
          title: 'PAGINA NON TROVATA',
          quote: 'La pagina richiesta non esiste o è stata spostata.',
          linkLabel: 'vai alla pagina iniziale',
          linkText: 'Torna alla pagina iniziale',
        },
        editLink: {
          pattern: 'https://github.com/xbit18/violarr/edit/main/docs/:path',
          text: 'Modifica questa pagina su GitHub',
        },
        footer: {
          message:
            'Violarr è stato creato e reso possibile anche grazie a strumenti di intelligenza artificiale generativa, sotto direzione e revisione umana.',
          copyright: 'Documentazione Violarr · Licenza MIT',
        },
      },
    },
    en: {
      label: 'English',
      lang: 'en-US',
      link: '/en/',
      title: 'Violarr',
      description: 'The Prowlarr integration for Il Corsaro Viola',
      themeConfig: {
        siteTitle: 'Violarr',
        nav: [
          { text: 'Getting started', link: '/en/getting-started/installation' },
          { text: 'Using Violarr', link: '/en/configuration/webui' },
          {
            text: 'Operations and recovery',
            link: '/en/getting-started/docker',
          },
          {
            text: 'Advanced and development',
            link: '/en/how-it-works/architecture',
          },
        ],
        sidebar: englishSidebar,
        outline: { label: 'On this page' },
        docFooter: { prev: 'Previous page', next: 'Next page' },
        editLink: {
          pattern: 'https://github.com/xbit18/violarr/edit/main/docs/:path',
          text: 'Edit this page on GitHub',
        },
        footer: {
          message:
            'Violarr was created and made possible in part through generative AI tools, under human direction and review.',
          copyright: 'Violarr documentation · MIT License',
        },
      },
    },
  },
  themeConfig: {
    logo: '/logo.png',
    socialLinks: [
      { icon: 'github', link: 'https://github.com/xbit18/violarr' },
    ],
    search: {
      provider: 'local',
      options: {
        locales: {
          root: {
            translations: {
              button: {
                buttonText: 'Cerca',
                buttonAriaLabel: 'Cerca',
              },
              modal: {
                displayDetails: 'Mostra dettagli',
                resetButtonTitle: 'Reimposta ricerca',
                backButtonTitle: 'Chiudi ricerca',
                noResultsText: 'Nessun risultato trovato',
                footer: {
                  selectText: 'Seleziona',
                  selectKeyAriaLabel: 'Invio',
                  navigateText: 'Naviga',
                  navigateUpKeyAriaLabel: 'Freccia su',
                  navigateDownKeyAriaLabel: 'Freccia giù',
                  closeText: 'Chiudi',
                  closeKeyAriaLabel: 'Esc',
                },
              },
            },
          },
        },
      },
    },
  },
})
