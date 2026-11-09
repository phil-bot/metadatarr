FROM node:22-alpine AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json ./
RUN npm install --no-package-lock
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./
COPY api/ ./api/
COPY core/ ./core/
COPY net/ ./net/
COPY plugins/ ./plugins/
COPY --from=frontend-build /build/frontend/dist ./static

# Keep the runtime image non-root. Compose may override this with PUID:PGID.
RUN useradd --system --uid 10001 app
USER app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"

CMD ["python", "app.py"]
