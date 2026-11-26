/** Lien de désinscription d'une newsletter, tiré de l'en-tête `List-Unsubscribe` (`entry.meta`) :
 * la page web d'abord, sinon l'adresse e-mail. Seuls `https:` et `mailto:` passent. */
export function unsubscribeLink(meta: Record<string, unknown> | null | undefined): string | null {
  const url = meta?.unsubscribe_url
  if (typeof url === 'string' && /^https:\/\//i.test(url)) return url
  const mailto = meta?.unsubscribe_mailto
  if (typeof mailto === 'string' && /^mailto:/i.test(mailto)) return mailto
  return null
}
