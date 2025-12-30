FROM python:3.11-slim

LABEL maintainer="Media Organizer"
LABEL description="Cloud-based media file organizer for Jellyfin"

# Install rclone and dependencies
RUN apt-get update && apt-get install -y \
    curl \
    unzip \
    && curl https://rclone.org/install.sh | bash \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create directories for data persistence
RUN mkdir -p /app/data /app/logs

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV ORGANIZER_CONFIG=/app/config.yaml
ENV ORGANIZER_DB=/app/data/media_organizer.db
ENV ORGANIZER_LOG_DIR=/app/logs

# Default command - run in daemon mode
CMD ["python", "main.py", "--daemon"]
