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

/** Nom proposé par le serveur (`Content-Disposition`), sinon `fallback`. */
export function filenameFrom(res: Response, fallback: string): string {
  const m = /filename="([^"]+)"/.exec(res.headers.get('content-disposition') ?? '')
  return m?.[1] ?? fallback
}
