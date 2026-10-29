/** Téléchargement d'un fichier obtenu par `fetch` authentifié (les liens directs n'envoient pas le jeton). */

export function saveBlob(blob: Blob, filename: string): void {
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  // Révocation différée : certains navigateurs (Firefox, Safari) annulent le téléchargement si l'URL
  // disparaît juste après le clic.
  window.setTimeout(() => URL.revokeObjectURL(a.href), 60_000)
}

/**
 * Téléchargement direct d'une URL (lien à usage unique, sans jeton dans les en-têtes) : le navigateur
 * écrit sur le disque au fil de la réception, sans copie en mémoire dans l'onglet, avec sa propre
 * progression. Le serveur doit répondre en `Content-Disposition: attachment`.
 */
export function downloadUrl(url: string): void {
  const a = document.createElement('a')
  a.href = url
  a.download = ''
  document.body.appendChild(a)
  a.click()
  a.remove()
}
