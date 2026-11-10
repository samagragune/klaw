import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Required by docker/docker-compose.yml, which runs .next/standalone/server.js.
  output: "standalone",
  // Every page in this dashboard is client-rendered against the backend's
  // authenticated API over a live WebSocket, so there is nothing for the
  // server to cache or partially prerender. Leaving Cache Components on just
  // produces "could not validate instant navigation" warnings for routes that
  // are dynamic by design.
  cacheComponents: false,
  partialPrefetching: false,
  // The floating dev overlay sits on top of the sidebar's connection
  // indicator, which is the one thing that must stay visible while debugging.
  devIndicators: false,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
