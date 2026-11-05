import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react(), {
    name: 'assessment-index-entry',
    enforce: 'post',
    generateBundle(_options, bundle) {
      const entry = bundle['assessment.html'];
      if (!entry || entry.type !== 'asset') throw new Error('Assessment entry missing');
      delete bundle['assessment.html'];
      entry.fileName = 'index.html';
      bundle['index.html'] = entry;
    },
  }],
  base: './',
  publicDir: false,
  build: {
    outDir: '../src/stalecheck/assessment_static',
    emptyOutDir: true,
    rollupOptions: { input: 'assessment.html' },
  },
  test: { environment: 'jsdom', setupFiles: ['./src/test-setup.ts'], restoreMocks: true, testTimeout: 15000 },
});
