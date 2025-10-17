# --- Stage 1: Base Image ---
FROM python:3.11-slim-bullseye

# --- Environment Variables ---
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# --- System Dependencies ---
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# --- Application Setup ---
WORKDIR /app

# Copy requirements file
COPY requirements.txt .

# Install Python packages (as root)
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY app.py .

# Create the music directory
RUN mkdir -p /app/music

# --- Port Exposure ---
EXPOSE 4000

# --- Run Command ---
# Everything runs as the default root user.
CMD ["gunicorn", "--bind", "0.0.0.0:4000", "--workers", "3", "app:app"]