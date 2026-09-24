import { defineConfig, loadEnv } from 'vite';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const configDir = fileURLToPath(new URL('.', import.meta.url));

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  return {
    root: resolve(configDir, 'static'),
    base: env.VITE_BASE_PATH || './',
    build: {
      outDir: resolve(configDir, '../docs'),
      emptyOutDir: true,
    },
  };
});
