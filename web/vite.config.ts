import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'
import { readFileSync } from 'node:fs'
import { VitePWA } from 'vite-plugin-pwa'

const { version } = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8')) as { version: string }

// https://vite.dev/config/
export default defineConfig({
  // Numéro de version dans le bundle : chaque version change donc sw.js (manifeste de précache), ce qui
  // remplace le service worker installé même quand seul le serveur a changé (en-têtes de sw.js compris).
  define: { __APP_VERSION__: JSON.stringify(version) },
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      // Mise à jour sur demande : un bandeau propose de recharger (UpdateBanner) plutôt qu'un rechargement
      // en pleine lecture. L'enregistrement se fait depuis le code (virtual:pwa-register/react).
      registerType: 'prompt',
      injectRegister: false,
      includeAssets: ['favicon.svg', 'apple-touch-icon.png'],
      manifest: {
        name: 'Fluxus',
        short_name: 'Fluxus',
        description: 'Lecteur RSS auto-hébergé avec résumés',
        lang: 'fr',
        theme_color: '#0f172a',
        background_color: '#0f172a',
        display: 'standalone',
        start_url: '/',
        icons: [
          { src: '/icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: '/icon-512.png', sizes: '512x512', type: 'image/png' },
          // Motif réduit dans la zone sûre (cercle de 80 %) : Android applique son propre masque.
          { src: '/icon-maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
        // Appui long sur l'icône de l'application installée.
        shortcuts: [
          { name: 'Non lus', url: '/unread', icons: [{ src: '/icon-192.png', sizes: '192x192', type: 'image/png' }] },
          { name: 'Favoris', url: '/starred', icons: [{ src: '/icon-192.png', sizes: '192x192', type: 'image/png' }] },
        ],
        // « Partager → Fluxus » : ouvre l'ajout de flux prérempli (Layout, route /partage).
        // Android place souvent l'adresse dans `text` : le front cherche une URL dans les trois champs.
        share_target: {
          action: '/partage',
          method: 'GET',
          params: { title: 'title', text: 'text', url: 'url' },
        },
      },
      workbox: {
        // L'application est "offline-first" via Dexie ; le service worker ne gère que la coquille
        // et les images en meilleur effort. Les appels API ne sont jamais mis en cache ici.
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [/^\/api\//],
        globPatterns: ['**/*.{js,css,html,svg,png,woff2}'],
        // Les polices sont découpées par alphabet ; on ne pré-cache que le latin, le reste vient à l'usage.
        globIgnores: ['**/*-cyrillic*', '**/*-greek*', '**/*-vietnamese*', '**/*-latin-ext*'],
        runtimeCaching: [
          {
            urlPattern: ({ request }) => request.destination === 'font',
            handler: 'CacheFirst',
            options: { cacheName: 'fluxus-fonts', expiration: { maxEntries: 60, maxAgeSeconds: 60 * 60 * 24 * 365 } },
          },
          {
            urlPattern: ({ request }) => request.destination === 'image',
            handler: 'StaleWhileRevalidate',
            options: {
              cacheName: 'fluxus-images',
              // Réponses opaques (images cross-origin) : Chrome les compte avec un rembourrage de plusieurs Mo ;
              // on borne le nombre et on purge en cas de quota dépassé pour ne jamais bloquer IndexedDB.
              expiration: { maxEntries: 200, maxAgeSeconds: 60 * 60 * 24 * 14, purgeOnQuotaError: true },
            },
          },
        ],
      },
    }),
  ],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
  build: {
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
})
