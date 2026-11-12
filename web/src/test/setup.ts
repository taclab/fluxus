/** Environnement minimal des tests : IndexedDB en mémoire et les deux globaux lus à l'ouverture de la base locale. */
import 'fake-indexeddb/auto'

const store = new Map<string, string>()
Object.assign(globalThis, {
  localStorage: {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, value),
    removeItem: (key: string) => void store.delete(key),
    clear: () => store.clear(),
  },
  window: { location: { origin: 'http://localhost' } },
})
