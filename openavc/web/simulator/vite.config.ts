import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Relative asset URLs, because this UI is served from two places: directly by
// the simulator process on :19500 (driver development), and proxied by the
// main server under /simulator/ so it works from another machine and through
// the cloud tunnel. An absolute /assets/... would 404 in the second case.
// https://vite.dev/config/
export default defineConfig({
  base: './',
  plugins: [react()],
})
