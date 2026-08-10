# Stage 1: install dependencies (cached unless lockfile changes)
FROM node:lts-slim AS deps
WORKDIR /build/web
RUN corepack enable && corepack prepare pnpm@10.5.0 --activate
COPY web/.npmrc web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml* ./
COPY web/packages/ ./packages/
COPY web/scripts/ ./scripts/
RUN pnpm install

# Stage 2: build (cached unless source changes)
FROM deps AS build
COPY web/ .
RUN NODE_OPTIONS=--max_old_space_size=4096 pnpm build

# Stage 3: serve with nginx
# 固定版本：nginx 1.31.3+ 在 CentOS 7（kernel 3.10）老内核上 pwrite /run/nginx.pid 报 EPERM 无法启动
FROM nginx:1.31.2-alpine
COPY deploy/web.conf /etc/nginx/conf.d/default.conf
COPY --from=build /build/web/dist /var/www/html/fast-soy-admin
COPY deploy/ssl/agent-deepseek.com_ca.crt /etc/nginx/ssl/agent-deepseek.com_ca.crt
COPY deploy/ssl/agent-deepseek.com.key    /etc/nginx/ssl/agent-deepseek.com.key
EXPOSE 80 443
