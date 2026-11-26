/** Copie un texte : API du presse-papiers, sinon sélection et `execCommand` (http hors localhost,
 * WebView anciennes). Renvoie faux si aucune des deux n'a fonctionné. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // Refus (contexte non sécurisé, permission) : on tente la méthode ancienne.
  }
  const area = document.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', '')
  area.style.position = 'fixed'
  area.style.opacity = '0'
  document.body.appendChild(area)
  area.select()
  try {
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    area.remove()
  }
}
