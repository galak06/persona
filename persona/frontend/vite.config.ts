import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// https://vite.dev/config/
// Tailwind 4 wires in through `@tailwindcss/vite` — no separate postcss
// config and no `tailwind.config.js` needed. Theme tokens live in
// `src/index.css` under `@theme`.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true,  // fail instead of auto-incrementing to 5174+
    // Backend dev server runs on 127.0.0.1:5001 — see VITE_API_URL in .env.
  },
});
