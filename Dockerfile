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
# Cluster Mode image registration requires a CLI newer than the SDK's service model.
RUN python3 -c "import urllib.request,hashlib,zipfile; p='/tmp/awscli.zip'; urllib.request.urlretrieve('https://awscli.amazonaws.com/awscli-exe-linux-x86_64-2.37.10.zip',p); assert hashlib.sha256(open(p,'rb').read()).hexdigest()=='1aaed142cb63294e41ef70431fe9c602dbb869f8a21b6b8fc9d5ffd53b5c9235'; zipfile.ZipFile(p).extractall('/tmp/awscli')" \
    && chmod +x /tmp/awscli/aws/install /tmp/awscli/aws/dist/aws /tmp/awscli/aws/dist/aws_completer \
    && /tmp/awscli/aws/install --install-dir /opt/aws-cli --bin-dir /usr/local/bin \
    && rm -rf /tmp/awscli /tmp/awscli.zip
WORKDIR /app
RUN python3 -c "import urllib.request,hashlib,tarfile,io,pathlib; b=urllib.request.urlopen('https://github.com/aquasecurity/trivy/releases/download/v0.76.1/trivy_0.76.1_Linux-64bit.tar.gz',timeout=60).read(); assert hashlib.sha256(b).hexdigest()=='c7cbe3137fb8a48a17574bede3dd6beb16742d1e9772929940fc90082c39d9c0'; p=pathlib.Path('/usr/local/bin/trivy'); p.write_bytes(tarfile.open(fileobj=io.BytesIO(b),mode='r:gz').extractfile('trivy').read()); p.chmod(0o755)"
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
    EBKIT_LIVE_ENABLED=false EBKIT_GITHUB_AUTO_DEPLOY=false EBKIT_PUBLIC_DEMO=true EBKIT_SOURCE_SCANNER=trivy
USER node
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 CMD ["node", "-e", "fetch('http://127.0.0.1:'+process.env.PORT+'/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
CMD ["node", "web/server/start.mjs"]
