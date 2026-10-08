import { defineConfig } from 'vitest/config';
// Unit tests exercise the fixture service; a developer's .env.local (VITE_DATA_MODE=api) must not switch them to a live backend.
export default defineConfig({
  test: {
    include: ['tests/*.test.ts'],
    env: { VITE_DATA_MODE: 'fixture', VITE_API_BASE_URL: '/api/v1' },
  },
});
