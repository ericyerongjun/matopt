FROM node:22-alpine AS libraries
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

FROM nginx:alpine
COPY frontend/sandbox/index.html frontend/sandbox/runtime.js /usr/share/nginx/html/
COPY --from=libraries /app/frontend/node_modules/plotly.js/dist/plotly.min.js /usr/share/nginx/html/plotly.min.js
COPY frontend/sandbox/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
