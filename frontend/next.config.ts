import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  // Emits a self-contained server bundle, so the runtime image carries only
  // what the app actually imports instead of the whole node_modules tree.
  output: "standalone",
  // The dashboard talks to the API from the browser with a bearer token, so
  // there is no server-side proxying to configure here.
};

export default config;
