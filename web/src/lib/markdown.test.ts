// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'

import { renderSummary, sanitizeArticle } from './markdown'

describe('sanitizeArticle', () => {
  it('retire scripts, gestionnaires, formulaires et feuilles de style', () => {
    const html = sanitizeArticle(
      '<p onclick="x()">Texte</p><script>alert(1)</script><style>body{display:none}</style>' +
        '<form action="https://pirate.example"><input name="mdp"><button>Valider</button></form>',
    )
    expect(html).toContain('Texte')
    for (const banned of ['<script', 'onclick', '<style', '<form', '<input', '<button']) expect(html).not.toContain(banned)
  })

  it('retire les attributs style et les classes empruntables à l’application', () => {
    const html = sanitizeArticle('<div class="fixed inset-0 z-50" style="position:fixed;inset:0">Superposition</div><pre class="language-js">x</pre>')
    expect(html).not.toContain('style=')
    expect(html).not.toContain('fixed')
    expect(html).toContain('class="language-js"')
  })

  it('durcit liens et images, ne garde que les lecteurs vidéo connus', () => {
    const html = sanitizeArticle(
      '<a href="https://exemple.fr">lien</a><img src="https://exemple.fr/i.png">' +
        '<iframe src="https://www.youtube.com/embed/abc"></iframe><iframe src="https://pirate.example/x"></iframe>',
    )
    expect(html).toContain('rel="noopener noreferrer"')
    expect(html).toContain('referrerpolicy="no-referrer"')
    expect(html).toContain('youtube.com/embed/abc')
    expect(html).not.toContain('pirate.example')
  })
})

describe('renderSummary', () => {
  it('rend le Markdown et garde la classe des horodatages', () => {
    const html = renderSummary('**Point** à [1:05]', true)
    expect(html).toContain('<strong>Point</strong>')
    expect(html).toContain('class="ts"')
    expect(html).toContain('data-ts="65"')
    expect(html).toContain('href="#t=65"')
  })

  it('convertit les horodatages avec ou sans heures', () => {
    expect(renderSummary('[12:30]', true)).toContain('data-ts="750"')
    expect(renderSummary('[1:02:03]', true)).toContain('data-ts="3723"')
    expect(renderSummary('[1:05]', false)).not.toContain('data-ts')
  })

  it('n’exécute rien de ce que le modèle écrit', () => {
    const html = renderSummary('<img src=x onerror="alert(1)"><form><input></form><span style="color:red" class="fixed">x</span>', false)
    for (const banned of ['onerror', '<form', '<input', 'style=', 'class="fixed"']) expect(html).not.toContain(banned)
  })
})
