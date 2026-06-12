/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    // In production (Vercel), /api/* is handled by the Python serverless function.
    // In local dev, we proxy to the local FastAPI backend.
    const isDev = process.env.NODE_ENV === 'development';
    if (!isDev) {
      return [];
    }
    return [
      {
        source: '/api/:path*',
        destination: 'http://127.0.0.1:8000/api/:path*',
      },
      {
        source: '/health',
        destination: 'http://127.0.0.1:8000/health',
      },
    ];
  },
};

export default nextConfig;
