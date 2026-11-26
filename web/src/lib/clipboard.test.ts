// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'

import { copyText } from './clipboard'

describe('copyText', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('utilise le presse-papiers quand il est disponible', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    expect(await copyText('abc@news.example.org')).toBe(true)
    expect(writeText).toHaveBeenCalledWith('abc@news.example.org')
  })

  it('se rabat sur execCommand si le presse-papiers refuse, sans laisser de champ', async () => {
    vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('refus')) } })
    const exec = vi.fn().mockReturnValue(true)
    Object.defineProperty(document, 'execCommand', { value: exec, configurable: true })
    expect(await copyText('x')).toBe(true)
    expect(exec).toHaveBeenCalledWith('copy')
    expect(document.querySelectorAll('textarea')).toHaveLength(0)
  })
})
