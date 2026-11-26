import { describe, expect, it } from 'vitest'

import { unsubscribeLink } from './unsubscribe'

describe('unsubscribeLink', () => {
  it('préfère la page web à l’adresse e-mail', () => {
    expect(unsubscribeLink({ unsubscribe_url: 'https://l.example/u?x=1', unsubscribe_mailto: 'mailto:u@l.example' })).toBe('https://l.example/u?x=1')
    expect(unsubscribeLink({ unsubscribe_mailto: 'mailto:u@l.example' })).toBe('mailto:u@l.example')
  })

  it('ignore tout autre schéma ou une valeur absente', () => {
    expect(unsubscribeLink({ unsubscribe_url: 'javascript:alert(1)' })).toBeNull()
    expect(unsubscribeLink({ unsubscribe_url: 'http://l.example/u' })).toBeNull()
    expect(unsubscribeLink({ unsubscribe_url: 42 })).toBeNull()
    expect(unsubscribeLink({})).toBeNull()
    expect(unsubscribeLink(undefined)).toBeNull()
  })
})
