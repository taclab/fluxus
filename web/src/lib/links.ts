/** N'ouvre que des liens http(s) : un flux ne doit pas pouvoir déclencher un autre schéma. */
export function openExternal(url: string | null | undefined) {
  if (url && /^https?:\/\//i.test(url)) window.open(url, '_blank', 'noopener')
}
