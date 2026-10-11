import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Override for side-by-side stacks (scripts/agents/ui-check.sh): VQ_API_PORT=8001.
const apiPort = process.env.VQ_API_PORT ?? "8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": `${import.meta.dirname}/src`,
    },
  },
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name(id: string) {
                if (id.includes("node_modules/react-dom/") || id.includes("node_modules/react/")) {
                  return "vendor-react";
                }
                if (id.includes("node_modules/recharts/") || id.includes("node_modules/d3-")) {
                  return "vendor-recharts";
                }
                // radix-ui is a re-export shim that Rolldown tree-shakes to nothing, so the
                // chunk has to match the @radix-ui/* packages that hold the code.
                if (id.includes("node_modules/radix-ui/") || id.includes("node_modules/@radix-ui/")) {
                  return "vendor-radix";
                }
                if (
                  id.includes("node_modules/@tanstack/react-router/") ||
                  id.includes("node_modules/@tanstack/react-query/")
                ) {
                  return "vendor-tanstack";
                }
                if (
                  id.includes("node_modules/plotly.js-dist-min/") ||
                  id.includes("node_modules/react-plotly.js/")
                ) {
                  return "vendor-plotly";
                }
                return null;
              },
            },
          ],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": `http://localhost:${apiPort}`,
      "/ws": { target: `ws://localhost:${apiPort}`, ws: true },
    },
  },
});
