# syntax=docker/dockerfile:1

# ========== Frontend Build Stage ==========
FROM node:22-bookworm-slim AS frontend
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html vite.config.ts tsconfig.json ./
COPY src ./src
RUN npm run build

# ========== Backend Runtime Stage ==========
FROM python:3.13-slim AS backend
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LAW_DATA_DIR=/app/data \
    LAW_DB_PATH=/app/data/jw-db-records-20261113.sqlite3 \
    LAW_PORT=8000
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY migration ./migration
RUN mkdir -p /app/data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=10m --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" || exit 1
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ========== Nginx Frontend Stage ==========
FROM nginx:alpine AS nginx-frontend
COPY --from=frontend /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
