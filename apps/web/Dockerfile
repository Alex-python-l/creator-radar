# 构建上下文 = 仓库根（pnpm workspace + packages/shared）
# build: docker build -f apps/web/Dockerfile .
FROM node:22-bookworm-slim AS base
ENV PNPM_HOME=/pnpm PATH=/pnpm:$PATH NEXT_TELEMETRY_DISABLED=1
RUN corepack enable

FROM base AS build
WORKDIR /repo
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/package.json
COPY packages/shared/package.json packages/shared/package.json
RUN pnpm install --frozen-lockfile
COPY . .
# 构建期跳过 env 校验（DATABASE_URL 等运行时才注入）
ENV SKIP_ENV_VALIDATION=1 NODE_ENV=production NODE_OPTIONS=--max-old-space-size=1536
RUN pnpm --filter web build

FROM base AS runner
ARG RELEASE_COMMIT
LABEL org.opencontainers.image.revision="${RELEASE_COMMIT}"
WORKDIR /app
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0
# standalone 输出已含精简 node_modules + packages/shared
COPY --from=build /repo/apps/web/.next/standalone ./
COPY --from=build /repo/apps/web/.next/static ./apps/web/.next/static
EXPOSE 3000
CMD ["node", "apps/web/server.js"]
