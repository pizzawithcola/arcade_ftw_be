# public.ecr.aws mirrors the Docker Hub library images without Docker Hub's
# anonymous pull rate limits, which can fail CI builds.
FROM public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY app ./app

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app

# Numeric form of the appuser above: no /etc/passwd lookup needed at start.
USER 1000:1000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
