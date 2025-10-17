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

# Copy requirements file first for layer caching
COPY requirements.txt .

# Install Python packages system-wide as root.
# This places 'gunicorn' in /usr/local/bin/, a standard system location.
RUN pip install --no-cache-dir -r requirements.txt

# Create the non-root user and the music directory
RUN useradd -m appuser && mkdir -p /app/music

# Copy the application code
COPY main.py .

# Change ownership of the entire app directory to the non-root user.
RUN chown -R appuser:appuser /app

# Switch to the non-root user to run the application securely.
USER appuser

# --- Port Exposure ---
EXPOSE 4000

# --- Run Command ---
# [--- THE DEFINITIVE FIX ---]: Use the absolute path to the gunicorn executable.
# This bypasses any PATH issues and tells the container exactly where to find the program.
CMD ["/usr/local/bin/gunicorn", "--bind", "0.0.0.0:4000", "--workers", "3", "app:app"]