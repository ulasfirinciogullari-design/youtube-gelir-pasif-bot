FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
CMD ["sh","-c","if [ \"${RAILWAY_SERVICE_NAME:-}\" = \"video-worker\" ]; then exec celery -A app.celery_app:celery worker --loglevel=INFO --concurrency=2; else exec uvicorn app.bootstrap:app --host 0.0.0.0 --port ${PORT:-8000}; fi"]
