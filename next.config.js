/** @type {import('next').NextConfig} */
const nextConfig = {
  webpack: (config) => {
    // Required for better-sqlite3 native module
    config.externals.push({ 'better-sqlite3': 'commonjs better-sqlite3' });
    return config;
  },
};

module.exports = nextConfig;
