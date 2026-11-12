import { describe, expect, it } from 'vitest'

import { sharedUrl } from './sharedUrl'

describe('sharedUrl', () => {
  it('prend l’adresse du champ url', () => {
    expect(sharedUrl(new URLSearchParams({ url: 'https://exemple.fr/flux.xml' }))).toBe('https://exemple.fr/flux.xml')
  })

  it('trouve l’adresse dans le texte partagé par Android, sans la ponctuation finale', () => {
    expect(sharedUrl(new URLSearchParams({ title: 'Un titre', text: 'Un titre https://exemple.fr/article?x=1).' }))).toBe('https://exemple.fr/article?x=1')
  })

  it('renvoie une chaîne vide sans adresse http(s)', () => {
    expect(sharedUrl(new URLSearchParams({ text: 'javascript:alert(1) ftp://exemple.fr' }))).toBe('')
  })
})
