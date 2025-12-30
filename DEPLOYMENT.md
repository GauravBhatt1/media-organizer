# Media File Organizer - Oracle VPS Deployment Guide

## Prerequisites

1. **Oracle VPS** with Ubuntu/Debian or any Linux distribution
2. **Python 3.8+** installed
3. **rclone** installed and configured with your remotes
4. **TMDB API key**

---

## Step 1: Install Dependencies

### Install Python and pip
```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv -y
```

### Install rclone (if not already installed)
```bash
curl https://rclone.org/install.sh | sudo bash
```

### Verify rclone remotes
```bash
rclone config
# Should show: anime, kdrama, movies, movies1, tvshows
```

---

## Step 2: Setup the Organizer

### Clone/Upload the organizer files
```bash
# Create directory
mkdir -p ~/media-organizer
cd ~/media-organizer

# Upload all files from the 'organizer' directory to this location
```

### Create Python virtual environment
```bash
python3 -m venv venv
source venv/bin/activate
```

### Install Python dependencies
```bash
pip install -r requirements.txt
```

---

## Step 3: Configuration

### Create environment file
```bash
cp .env.example .env
nano .env
```

Add your TMDB API key:
```
TMDB_API_KEY=07204b56f116aa64c5b68ec20c12ae75
```

### Review config.yaml
```bash
nano config.yaml
```

Key settings to verify:
- `scan_remotes` - List of your rclone remotes
- `scan.interval_minutes` - How often to check for new files (default: 5)
- `scan.stability_check_seconds` - Wait time before processing (default: 120)

---

## Step 4: Test the Organizer

### Run a dry-run first (no files moved)
```bash
source venv/bin/activate
python main.py --dry-run --once
```

### Check status
```bash
python main.py --status
```

### Run once (actually move files)
```bash
python main.py --once
```

---

## Step 5: Setup as Systemd Service

### Create service file
```bash
sudo nano /etc/systemd/system/media-organizer.service
```

Paste this content (adjust paths as needed):
```ini
[Unit]
Description=Media File Organizer for Jellyfin
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/media-organizer
Environment=PATH=/home/ubuntu/media-organizer/venv/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/home/ubuntu/media-organizer/venv/bin/python main.py --daemon
Restart=always
RestartSec=10

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=media-organizer

# Security
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/ubuntu/media-organizer

[Install]
WantedBy=multi-user.target
```

### Enable and start the service
```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable on boot
sudo systemctl enable media-organizer

# Start the service
sudo systemctl start media-organizer

# Check status
sudo systemctl status media-organizer
```

### View logs
```bash
# Live logs
sudo journalctl -u media-organizer -f

# Last 100 lines
sudo journalctl -u media-organizer -n 100

# Or check the log file
tail -f ~/media-organizer/organizer.log
```

---

## Step 6: Management Commands

### Stop the service
```bash
sudo systemctl stop media-organizer
```

### Restart the service
```bash
sudo systemctl restart media-organizer
```

### Run manual scan
```bash
cd ~/media-organizer
source venv/bin/activate
python main.py --once
```

### Check pending files
```bash
python main.py --status
```

---

## Troubleshooting

### rclone errors
```bash
# Test rclone access
rclone lsf movies: --max-depth 1

# Check rclone config
rclone config show
```

### TMDB errors
- Verify API key is correct in `.env`
- Check API rate limits (max 40 requests/10 seconds)

### Files not being processed
1. Check if file size is stable (wait 2+ minutes)
2. Check logs: `tail -f organizer.log`
3. Run with debug: Edit config.yaml, set `logging.level: DEBUG`

### Database issues
```bash
# Reset database (will reprocess all files)
rm organizer.db
```

---

## Folder Structure After Organization

### Movies
```
movies:/
├── Inception (2010)/
│   └── Inception (2010) - 1080p.mkv
├── Avatar (2009)/
│   └── Avatar (2009) - 2160p.mkv
```

### TV Shows / Anime / K-Drama
```
tvshows:/
├── Breaking Bad (2008)/
│   ├── Season 01/
│   │   ├── Breaking Bad S01E01.mkv
│   │   └── Breaking Bad S01E02.mkv
│   └── Season 02/
│       └── Breaking Bad S02E01.mkv

anime:/
├── Attack on Titan (2013)/
│   └── Season 01/
│       └── Attack on Titan S01E01.mkv
```

---

## Quality Replacement Logic

When a higher quality version arrives:
1. New HD file is moved to destination FIRST
2. Move is verified successful
3. ONLY THEN is the old CAM file deleted
4. If move fails, old file is PRESERVED (no data loss)

Priority order (lowest to highest):
`CAM < HDTS < HDTC < 720p < 1080p < 2160p`
