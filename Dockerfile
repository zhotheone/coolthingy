# --- Stage 1: Base Image ---
FROM python:3.11-slim-bullseye

# --- Environment Variables ---
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
# [--- FIX #1 ---]: Explicitly add the default user package installation path to the system PATH.
# This ensures that executables like 'gunicorn' installed by 'appuser' can be found.
ENV PATH="/home/appuser/.local/bin:${PATH}"

# --- System Dependencies ---
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# --- Application Setup ---
WORKDIR /app

# [--- FIX #2 ---]: Create the non-root user first.
RUN useradd -m appuser

# Copy requirements file and change its ownership
COPY --chown=appuser:appuser requirements.txt .

# Switch to the non-root user BEFORE installing dependencies.
# This ensures that pip installs packages owned by the correct user.
USER appuser

# Install Python dependencies into the user's site-packages.
RUN pip install --no-cache-dir --user -r requirements.txt

# Switch back to the root user for commands that require root privileges.
USER root

# Create the music directory (which will be a mount point)
RUN mkdir -p /app/music

# Copy the rest of the application code
COPY --chown=appuser:appuser main.py .

# Change ownership of the entire app directory to the non-root user.
# This is crucial for allowing the app to write to the music directory.
RUN chown -R appuser:appuser /app

# Switch back to the non-root user to run the application securely.
USER appuser

# --- Port Exposure ---
EXPOSE 4000

# --- Run Command ---
# The CMD now correctly finds 'gunicorn' in the PATH.
CMD ["gunicorn", "--bind", "0.0.0.0:4000", "--workers", "3", "app:app"]