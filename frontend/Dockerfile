# Frontend image: build the SPA, serve it from nginx.
#
# Build context is ./frontend, so a backend-only change can't invalidate
# these layers (and vice versa) — that's most of the point of the split.
FROM node:20-alpine AS build
WORKDIR /build
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY . ./
# `npm run build` is tsc --noEmit && vite build, so a type error fails the
# image build rather than shipping a broken bundle.
RUN npm run build

# nginx-unprivileged (not stock nginx) so the pod keeps runAsNonRoot:true —
# it already runs as UID 101 and listens on 8080 instead of 80.
FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime
COPY --from=build /build/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
