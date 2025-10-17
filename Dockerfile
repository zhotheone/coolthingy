# --- Stage 1: Base Image ---
# Use an official, slim Python image for a smaller footprint.
FROM python:3.11-slim-bullseye

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN mkdir -p /app/music && useradd -m appuser
COPY main.py .
RUN chown -R appuser:appuser /app
USER appuser
EXPOSE 4000

CMD ["gunicorn", "--bind", "0.0.0.0:4000", "--workers", "3", "app:app"]