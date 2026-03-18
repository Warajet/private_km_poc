# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 – Builder
# Compiles the Next.js app and native modules (better-sqlite3) for Linux.
# ─────────────────────────────────────────────────────────────────────────────
FROM node:20-slim AS builder

# Build tools required by better-sqlite3 (native C++ addon)
RUN apt-get update && apt-get install -y \
    python3 make g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Cache the dependency layer — only re-runs when package files change
COPY package*.json ./
RUN npm ci

# Copy source and build
COPY . .
RUN npm run build

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 – Production runtime
# Minimal image that only carries what is needed to run the app.
# ─────────────────────────────────────────────────────────────────────────────
FROM node:20-slim AS runner

WORKDIR /app

# NODE_ENV must be "production" so Next.js skips dev-only overhead.
# PORT is set by Cloud Run automatically (default 8080); next start respects it.
ENV NODE_ENV=production

# Non-root user for security (matches Cloud Run's recommended practices)
RUN groupadd --system --gid 1001 nodejs && \
    useradd  --system --uid 1001 --gid nodejs nextjs

# Carry over compiled node_modules from the builder stage.
# better-sqlite3's pre-compiled .node binary is included here.
COPY --from=builder --chown=nextjs:nodejs /app/node_modules ./node_modules

# Next.js build output
COPY --from=builder --chown=nextjs:nodejs /app/.next ./.next

# Static assets and config
COPY --from=builder --chown=nextjs:nodejs /app/public    ./public
COPY --from=builder --chown=nextjs:nodejs /app/next.config.js ./next.config.js
COPY --from=builder --chown=nextjs:nodejs /app/package.json   ./package.json

# Persistent directory for the SQLite database file.
# NOTE: On Cloud Run this directory is EPHEMERAL (resets on restart).
# For production, replace SQLite with Cloud SQL — see DEPLOYMENT.md.
RUN mkdir -p /app/data && chown nextjs:nodejs /app/data

USER nextjs

# Cloud Run forwards traffic to this port (set via the PORT env var).
EXPOSE 8080

# `next start` reads process.env.PORT, which Cloud Run sets to 8080.
CMD ["npm", "start"]
