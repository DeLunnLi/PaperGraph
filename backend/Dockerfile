FROM python:3.11-slim

WORKDIR /app

# System deps: gcc for C extensions, tesseract for OCR, libmagic for file detection
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libffi-dev libmagic1 \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Create data dirs
RUN mkdir -p /app/data /app/downloads/papers

# Default env (can be overridden by docker-compose env)
ENV PAPERGRAPH_DATA_DIR=/app/data \
    PAPERGRAPH_DOWNLOADS_DIR=/app/downloads/papers \
    HOST=0.0.0.0 \
    PORT=8000 \
    PAPERGRAPH_UVICORN_RELOAD=0 \
    FZ_LOAD_SYSTEM_CMS=0

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=15s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["python", "run.py"]
