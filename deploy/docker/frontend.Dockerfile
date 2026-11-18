# syntax=docker/dockerfile:1.7
#
# CLIPFORGE web app (Next.js standalone server).
# Behind a TLS-inspecting proxy: docker build --secret id=extra_ca,src=/path/to/proxy-ca.pem ...

ARG NODE_IMAGE=node:22-bookworm-slim

FROM ${NODE_IMAGE} AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/extra_ca; fi; \
    npm ci --no-audit --no-fund
COPY frontend/ ./
# Where the Next.js server forwards /api requests when it is reached directly
# (the bundled Caddy normally routes /api straight to the API). Fixed at build time.
ARG CLIPFORGE_API_URL=http://api:8000
ENV CLIPFORGE_API_URL=${CLIPFORGE_API_URL}
RUN npm run build

FROM ${NODE_IMAGE} AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
