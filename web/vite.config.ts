import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
export default defineConfig({ plugins: [react()], test: { include: ['src/**/*.test.{ts,tsx}', 'server/*.test.mjs'] }, server: { port: 5173, strictPort: true, proxy: { '/api': 'http://127.0.0.1:8787' } }, preview: { proxy: { '/api': 'http://127.0.0.1:8787' } } });
