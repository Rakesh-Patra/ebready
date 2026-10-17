FROM node:22-bookworm-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392 AS frontend
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
ARG VITE_INSFORGE_URL
ARG VITE_INSFORGE_ANON_KEY
ENV VITE_INSFORGE_URL=$VITE_INSFORGE_URL VITE_INSFORGE_ANON_KEY=$VITE_INSFORGE_ANON_KEY
RUN npm run build

FROM node:22-bookworm-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392 AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY ebkit/ ./ebkit/
RUN python3 -m venv /opt/ebkit \
    && /opt/ebkit/bin/pip install --no-cache-dir --upgrade 'setuptools>=78.1.1' \
    && /opt/ebkit/bin/pip install --no-cache-dir '.[database]' \
    && /opt/ebkit/bin/pip uninstall --yes pip setuptools wheel
COPY web/package.json web/package-lock.json ./web/
RUN cd web && npm ci --omit=dev && npm cache clean --force \
    && rm -rf /usr/local/lib/node_modules/npm /opt/yarn* \
    && rm -f /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/yarn /usr/local/bin/yarnpkg
COPY web/server/ ./web/server/
COPY --from=frontend /build/web/dist/ ./web/dist/
RUN mkdir -p /app/web/server/.jobs && chown -R node:node /app/web/server/.jobs
ENV NODE_ENV=production HOST=0.0.0.0 PORT=8080 EBKIT_PYTHON=/opt/ebkit/bin/python \
    EBKIT_STATIC_ROOT=/app/web/dist EBKIT_JOBS_DIR=/app/web/server/.jobs \
    EBKIT_LIVE_ENABLED=false EBKIT_GITHUB_AUTO_DEPLOY=false EBKIT_PUBLIC_DEMO=true
USER node
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 CMD ["node", "-e", "fetch('http://127.0.0.1:'+process.env.PORT+'/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
CMD ["node", "web/server/start.mjs"]
