# Dashboard image. Next.js standalone output keeps the runtime layer small.
FROM node:22-alpine AS deps
WORKDIR /app
COPY dashboard/package*.json ./
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY dashboard/ ./
# Public env vars are inlined at build time, so they must be present here.
ARG NEXT_PUBLIC_RELIABILITY_API_URL=http://127.0.0.1:8000
ENV NEXT_PUBLIC_RELIABILITY_API_URL=$NEXT_PUBLIC_RELIABILITY_API_URL
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
