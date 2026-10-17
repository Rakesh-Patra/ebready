import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
const proxy = { '/api': 'http://127.0.0.1:8787', '/insforge': 'http://127.0.0.1:8787' };
export default defineConfig({ plugins: [react()], test: { include: ['src/**/*.test.{ts,tsx}', 'server/*.test.mjs'] }, server: { port: 5173, strictPort: true, proxy }, preview: { proxy } });
