# Faultline UI — build the Vite SPA, serve it as static assets via NON-ROOT nginx (reverse-
# proxying /api to the BFF). No Node at runtime; the build runs in a throwaway stage.
#
# The bundle talks to a RELATIVE /api, so the same image runs in every environment — only
# API_UPSTREAM (the proxy target) varies at run time. No backend URL is baked into the JavaScript.
# Build from the REPO ROOT so the shared .dockerignore applies:
#   docker build -f ui/app/Dockerfile -t qlp-ui .
#   docker run -p 8081:8080 -e API_UPSTREAM=host.docker.internal:8088 qlp-ui

# ---- build stage --------------------------------------------------------------
# Pin the node minor to the dev toolchain (ui/CLAUDE.md: node-v24.14.0); bump deliberately.
FROM node:24.14.0-slim AS build
WORKDIR /app
COPY ui/app/package.json ui/app/package-lock.json ./
RUN npm ci
COPY ui/app/ ./
RUN npm run build

# ---- runtime stage ------------------------------------------------------------
# Unprivileged nginx: runs as uid 101 and listens on 8080 out of the box (no root, no
# cap_net_bind gymnastics) — ALB/target-group friendly.
FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime

# Where nginx forwards /api/*. Overridable at run time (compose / helm / `docker run -e` set this).
ENV API_UPSTREAM=bff:8088

# Static assets.
COPY --from=build /app/dist /usr/share/nginx/html

# nginx's entrypoint runs envsubst over /etc/nginx/templates/*.template at start, writing the result
# to /etc/nginx/conf.d/ — that's how ${API_UPSTREAM} is substituted at run time rather than baked in.
COPY ui/app/nginx/default.conf.template /etc/nginx/templates/default.conf.template

EXPOSE 8080
