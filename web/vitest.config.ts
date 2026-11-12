import { defineConfig } from 'vitest/config'

// Tests des règles pures du front (sans navigateur) ; les parcours d'interface restent dans scripts/e2e-*.py.
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
    setupFiles: ['src/test/setup.ts'],
  },
})
