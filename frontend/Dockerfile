# syntax=docker/dockerfile:1

# --- Build stage ------------------------------------------------------------ #
FROM node:22-alpine AS builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# The API base URL is *baked into the browser bundle* — Next inlines every
# NEXT_PUBLIC_* value at build time, so it cannot be supplied at runtime. That
# makes a missing value a silent disaster: the page loads and every request
# goes to localhost. Fail the build instead.
ARG NEXT_PUBLIC_API_BASE_URL
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}
RUN test -n "$NEXT_PUBLIC_API_BASE_URL" || { \
      echo "ERROR: NEXT_PUBLIC_API_BASE_URL is required at build time."; \
      echo "       It is compiled into the browser bundle and cannot be set later."; \
      echo "       Example: --build-arg NEXT_PUBLIC_API_BASE_URL=https://api.example.com/api"; \
      exit 1; \
    }

ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build


# --- Runtime stage ---------------------------------------------------------- #
FROM node:22-alpine AS runtime

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

WORKDIR /app

# Never run as root.
RUN addgroup --system --gid 10001 nodejs \
    && adduser --system --uid 10001 --ingroup nodejs nextjs

# `standalone` carries its own minimal node_modules and server.js; the static
# assets and the public folder are the two things it does not copy itself.
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:'+(process.env.PORT||3000)+'/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
