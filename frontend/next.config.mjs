/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The control centre is a pure client of the FastAPI service; it holds no
  // server-side secrets and never imports backend code.
  env: {
    NEXT_PUBLIC_API_BASE_URL:
      process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api",
  },
};

export default nextConfig;
