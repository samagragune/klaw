# Backend image. Builds the API and the supervision sweeper.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY sdk/ ./sdk/

WORKDIR /app/backend

EXPOSE 8000

# One worker: the in-process event bus only fans out within a single process.
# Scale past one worker only with EVENT_BUS=redis. Hosts that assign a port
# (Railway, Fly, Render) pass it as $PORT.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
