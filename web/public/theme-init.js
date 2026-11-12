// Applique le thème et la typographie avant le premier rendu pour éviter tout flash.
// Fichier externe (et non inline) pour rester compatible avec une CSP `script-src 'self'`.
;(function () {
  try {
    var t = localStorage.getItem('fluxus.theme') || 'system'
    var dark = t === 'dark' || (t === 'system' && matchMedia('(prefers-color-scheme: dark)').matches)
    if (dark) document.documentElement.classList.add('dark')
    var r = JSON.parse(localStorage.getItem('fluxus.reading') || '{}')
    var d = document.documentElement.dataset
    d.readingFont = r.font || 'editorial'
    d.readingSize = r.size || 'm'
    d.readingWidth = r.width || 'normal'
  } catch {
    // Stockage local indisponible ou corrompu : thème et typographie par défaut.
  }
})()
