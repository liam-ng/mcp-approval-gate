# Frontend image: build the SPA, serve it from nginx.
# Build context is ./frontend
FROM node:lts-alpine AS build
WORKDIR /build
COPY package.json package-lock.json* ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY . ./
# `npm run build` is tsc --noEmit && vite build, so a type error fails the image build.
RUN npm run build

# nginx-unprivileged pod keeps runAsNonRoot:true so it runs as UID 101 and listens on 8080 instead of 80.
FROM nginxinc/nginx-unprivileged:stable-alpine AS runtime
COPY --from=build /build/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
