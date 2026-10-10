# Build from the repository root: docker build -f frontend/Dockerfile .
# The native frontend is self-contained under frontend/.
FROM node:22-alpine AS build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ARG VITE_UI_SANDBOX_ORIGIN=""
ENV VITE_UI_SANDBOX_ORIGIN=$VITE_UI_SANDBOX_ORIGIN
RUN npm run build

# ── Serve with lightweight static server ─────────────────────────────────
FROM nginx:alpine

# Copy built assets
COPY --from=build /app/frontend/dist /usr/share/nginx/html

# The official image substitutes only environment variables in this template;
# Nginx variables such as $host and $scheme remain intact.
ENV UI_SANDBOX_ORIGIN=""
COPY frontend/nginx.conf.template /etc/nginx/templates/default.conf.template

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
