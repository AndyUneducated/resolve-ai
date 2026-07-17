# ResolveAI web image (M15 · one-click full stack).
#
# Build context MUST be the repo root (see docker-compose.full.yml).
#
# NOTE: NEXT_PUBLIC_API_URL is baked at *build* time (next.config.js exposes it
# to the browser bundle). The browser runs on the host, so it must resolve the
# API on the host-published port — default http://localhost:8000, NOT the
# compose-internal http://api:8000.
FROM node:22-bookworm-slim AS build

WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1

COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci

COPY apps/web ./

ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN npm run build

# --- Runtime ----------------------------------------------------------------
FROM node:22-bookworm-slim AS run

WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1

# `next start` needs the build output + node_modules + config.
COPY --from=build /app ./

EXPOSE 3000

HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=6 \
    CMD node -e "require('http').get('http://localhost:3000',r=>process.exit(r.statusCode<500?0:1)).on('error',()=>process.exit(1))"

CMD ["npm", "start"]
