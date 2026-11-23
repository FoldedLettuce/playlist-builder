FROM node:22-alpine
ENV NODE_ENV=production
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force

COPY --chown=node:node server.js ROADMAP.md ./
COPY --chown=node:node public ./public
COPY --chown=node:node admin ./admin
COPY --chown=node:node data ./data

# permanent plan history lives here, mount a volume on it
RUN mkdir -p /app/db && chown node:node /app/db
VOLUME /app/db

USER node
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD wget -qO- http://127.0.0.1:3000/healthz || exit 1

CMD ["node", "--no-warnings", "server.js"]
