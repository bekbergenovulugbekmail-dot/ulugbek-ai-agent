/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Ship a self-contained server: `.next/standalone` carries only the modules
  // actually reached, which is what makes the deployed image small enough to
  // rebuild on every push.
  output: "standalone",
  // The control centre is a pure client of the FastAPI service; it holds no
  // server-side secrets and never imports backend code.
  env: {
    NEXT_PUBLIC_API_BASE_URL:
      process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api",
  },
};

export default nextConfig;
