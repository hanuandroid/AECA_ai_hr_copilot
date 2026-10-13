# Stages (production hardening Phase 2):
#   deps / build / prod-deps   Node 24 LTS (pinned), lockfile installs
#   production                 `next start` as the non-root `node` user   -> docker-compose.prod.yml (target: production)
#   dev                        default/last stage, unchanged dev behaviour -> docker-compose.yml
ARG NODE_PROD_IMAGE=node:24.21.0-alpine

FROM ${NODE_PROD_IMAGE} AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM deps AS build
# Next inlines NEXT_PUBLIC_* at build time, so the public API URL is a build argument.
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL} \
    NEXT_TELEMETRY_DISABLED=1
COPY . .
RUN npm run build

FROM ${NODE_PROD_IMAGE} AS prod-deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

FROM ${NODE_PROD_IMAGE} AS production
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000
COPY --from=prod-deps --chown=root:root /app/node_modules ./node_modules
# next.config.ts is loaded with TypeScript at `next start`; without it Next tries to install TypeScript from the network
# at startup. Ship the exact lockfile version (it has no dependencies) so the container never fetches packages at runtime.
COPY --from=deps --chown=root:root /app/node_modules/typescript ./node_modules/typescript
COPY --from=build --chown=root:root /app/.next ./.next
COPY --from=build --chown=root:root /app/public ./public
COPY --from=build --chown=root:root /app/package.json /app/next.config.ts ./
USER node
EXPOSE 3000
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/login').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"]
# Run Next directly (not through npm) so SIGTERM reaches the server for a graceful stop.
CMD ["node", "node_modules/next/dist/bin/next", "start", "-p", "3000"]

FROM node:20-alpine AS dev
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
CMD ["npm", "run", "dev"]
