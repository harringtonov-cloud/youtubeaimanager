/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  poweredByHeader: false,
  eslint: { ignoreDuringBuilds: true }, // type-checking is enforced by `tsc --noEmit` in CI
};

export default nextConfig;
