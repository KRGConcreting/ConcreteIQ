FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for WeasyPrint
RUN apt-get update && apt-get install -y \
    build-essential \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway sets $PORT; fallback to 8000 for local Docker
ENV PORT=8000
EXPOSE ${PORT}

RUN useradd -m appuser
USER appuser

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 2
