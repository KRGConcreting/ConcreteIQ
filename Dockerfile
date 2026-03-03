FROM python:3.12-slim

WORKDIR /app

# Install system dependencies (fonts for ReportLab PDF generation)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway sets $PORT; fallback to 8000 for local Docker
ENV PORT=8000
EXPOSE ${PORT}

RUN useradd -m appuser
USER appuser

# Single worker — in-memory rate limiting requires single process.
# Single-user app, async handles concurrency fine.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1
