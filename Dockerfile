FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build
FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml ./
COPY backend ./backend
RUN pip install --no-cache-dir .
COPY assets ./assets
COPY custom_components ./custom_components
COPY --from=frontend /build/dist ./frontend/dist
ENV EPAPERS_HOST=0.0.0.0 EPAPERS_DATA_DIR=/data EPAPERS_RESOURCE_DIR=/app
EXPOSE 8099
CMD ["python","-m","epapers"]
