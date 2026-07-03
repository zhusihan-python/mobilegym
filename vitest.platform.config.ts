import path from 'path';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  resolve: {
    alias: {
      '@': path.resolve(__dirname, '.'),
    },
  },
  test: {
    include: ['tests/testPlatform*.test.ts', 'tests/testPlatform*.test.tsx'],
    environment: 'jsdom',
    globals: false,
  },
});
