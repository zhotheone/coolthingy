# --- Stage 1: Base Image ---
# Use an official, slim Python image.
FROM python:3.11-slim-bullseye

# --- Environment Variables ---
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# --- System Dependencies (run as root) ---
# Install ffmpeg, which is required by yt-dlp.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# --- Application Setup (run as root) ---
# Set the working directory for the rest of the build.
WORKDIR /app

# Copy and install Python dependencies system-wide.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create the non-root user that will run the application.
RUN useradd -m appuser
RUN mkdir -p /app/music
COPY . .
RUN chown -R appuser:appuser /app

# --- Final Secure Runtime ---
# Switch to the non-root user for running the application.
USER appuser

# Inform Docker that the container listens on port 4000.
EXPOSE 4000

# Run the application using the robust python -m gunicorn command.
CMD ["python", "-m", "gunicorn", "--bind", "0.0.0.0:4000", "--workers", "3", "main:app"]