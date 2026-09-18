import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  // The dashboard talks to the API from the browser with a bearer token, so
  // there is no server-side proxying to configure here.
};

export default config;
