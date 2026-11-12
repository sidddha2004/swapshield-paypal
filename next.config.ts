import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  env: {
    // Hosted builds on Vercel have no Python/GPU API, so they replay recorded results
    // (lib/demo.ts). Local runs stay live unless NEXT_PUBLIC_SWAPSHIELD_DEMO is set.
    NEXT_PUBLIC_SWAPSHIELD_DEMO:
      process.env.NEXT_PUBLIC_SWAPSHIELD_DEMO ?? (process.env.VERCEL ? "true" : "false"),
  },
};

export default nextConfig;
