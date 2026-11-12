/** Adresse reçue par le partage système (`share_target` du manifeste, route /partage). Android met
 * souvent l'adresse dans `text`, parfois précédée du titre de la page : on prend la première URL trouvée. */
export function sharedUrl(params: URLSearchParams): string {
  for (const key of ['url', 'text', 'title']) {
    const match = params.get(key)?.match(/https?:\/\/[^\s<>"]+/)
    if (match) return match[0].replace(/[).,;:!?»]+$/, '')
  }
  return ''
}
