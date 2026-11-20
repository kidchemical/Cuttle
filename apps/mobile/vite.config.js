import { copyFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';

const here = (p) => fileURLToPath(new URL(p, import.meta.url));

// The shared Cuttle error page (src/web/error_page.html) ships inside the APK so
// MainActivity can show it while the server is unreachable.
const sharedErrorPage = {
  name: 'cuttle-error-page',
  closeBundle() {
    copyFileSync(here('../../src/web/error_page.html'), here('./dist/error_page.html'));
  },
};

export default defineConfig({
  root: '.',
  plugins: [sharedErrorPage],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
});
