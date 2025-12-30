# Docker / Portainer Deployment Guide

This guide explains how to deploy the Media File Organizer using Docker or Portainer.

## Prerequisites

1. Docker installed on your VPS
2. rclone configured with your remotes (anime, kdrama, movies, movies1, tvshows)
3. TMDB API key

## Quick Start

### Option 1: Using Docker Compose (Recommended)

1. **Copy the organizer folder to your VPS:**
   ```bash
   scp -r organizer/ user@your-vps:/opt/media-organizer/
   ```

2. **Create data directories:**
   ```bash
   cd /opt/media-organizer
   mkdir -p data logs
   ```

3. **Set your rclone config path (choose one method):**
   
   **Method A - Environment variable:**
   ```bash
   export RCLONE_CONFIG_DIR=/home/youruser/.config/rclone
   docker-compose up -d
   ```
   
   **Method B - Edit docker-compose.yml directly:**
   ```yaml
   volumes:
     # Replace with your actual path (do NOT use ~)
     - /home/youruser/.config/rclone:/root/.config/rclone:ro
   ```

4. **Start the container:**
   ```bash
   docker-compose up -d
   ```

5. **Check logs:**
   ```bash
   docker-compose logs -f
   ```

### Option 2: Using Portainer

1. **In Portainer, go to Stacks > Add Stack**

2. **Paste the docker-compose.yml content (update the rclone path!):**
   ```yaml
   version: "3.8"

   services:
     media-organizer:
       build: .
       container_name: media-organizer
       restart: unless-stopped
       
       environment:
         - TMDB_API_KEY=07204b56f116aa64c5b68ec20c12ae75
         - SCAN_INTERVAL=300
         - DRY_RUN=false
       
       volumes:
         # IMPORTANT: Replace /home/ubuntu with your actual home directory path
         # Do NOT use ~ as Docker does not expand it
         - /home/ubuntu/.config/rclone:/root/.config/rclone:ro
         - ./data:/app/data
         - ./logs:/app/logs
       
       deploy:
         resources:
           limits:
             memory: 512M
   ```

3. **Click Deploy**

### Option 3: Build and Run Manually

```bash
# Build the image
cd /opt/media-organizer
docker build -t media-organizer .

# Run the container (replace /home/ubuntu with your actual home directory)
docker run -d \
  --name media-organizer \
  --restart unless-stopped \
  -e TMDB_API_KEY=07204b56f116aa64c5b68ec20c12ae75 \
  -e SCAN_INTERVAL=300 \
  -v /home/ubuntu/.config/rclone:/root/.config/rclone:ro \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  media-organizer
```

> **Important:** Do NOT use `~` in Docker volume paths - it won't be expanded. Use the full absolute path like `/home/ubuntu/.config/rclone`.

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `TMDB_API_KEY` | Your TMDB API key (required) | - |
| `SCAN_INTERVAL` | Scan interval in seconds | 300 (5 min) |
| `DRY_RUN` | Set to "true" for test mode | false |
| `LOG_LEVEL` | Logging level (DEBUG, INFO, WARNING) | INFO |
| `ORGANIZER_DB` | Database file path | /app/data/media_organizer.db |
| `ORGANIZER_LOG_DIR` | Log directory | /app/logs |

## Volume Mounts

| Container Path | Description |
|----------------|-------------|
| `/root/.config/rclone` | rclone configuration (read-only) |
| `/app/data` | SQLite database storage |
| `/app/logs` | Log files |
| `/app/config.yaml` | Custom config (optional) |

## Verify rclone Configuration

Before starting, verify your rclone remotes are accessible inside the container:

```bash
# Replace /home/ubuntu with your actual home directory
docker run --rm \
  -v /home/ubuntu/.config/rclone:/root/.config/rclone:ro \
  media-organizer \
  rclone listremotes
```

Expected output:
```
anime:
kdrama:
movies:
movies1:
tvshows:
```

## Useful Commands

```bash
# View real-time logs
docker logs -f media-organizer

# Check container status
docker ps | grep media-organizer

# Run a manual scan
docker exec media-organizer python main.py --once

# Check status
docker exec media-organizer python main.py --status

# Run in dry-run mode (test without moving files)
docker exec media-organizer python main.py --once --dry-run

# Stop the container
docker stop media-organizer

# Restart the container
docker restart media-organizer

# Remove and rebuild
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

## Troubleshooting

### Container exits immediately
Check the logs:
```bash
docker logs media-organizer
```

Common issues:
- Missing rclone config: Verify the volume mount path
- Invalid TMDB API key: Check the environment variable
- Missing config.yaml: Ensure the file exists in the build context

### rclone remotes not found
Verify your rclone.conf path and permissions:
```bash
ls -la ~/.config/rclone/rclone.conf
cat ~/.config/rclone/rclone.conf | head -5
```

### Permission issues
Ensure the data and logs directories are writable:
```bash
chmod 777 data logs
```

### Database locked errors
Only one instance should run at a time. Stop any existing containers:
```bash
docker stop media-organizer
```

## Health Check

The container includes a health check. View health status:
```bash
docker inspect --format='{{.State.Health.Status}}' media-organizer
```

## Updating

To update the organizer:

```bash
cd /opt/media-organizer
git pull  # or copy new files
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```
