FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN useradd -m appuser
USER appuser

# Cloud Run sets $PORT. ws ping 20 s closes dead server sockets.
# No --proxy-headers: client IP is read from X-Forwarded-For counting from the right (spoof-proof).
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} \
    --ws-ping-interval 20 --ws-ping-timeout 20 --ws-max-size 1048576
