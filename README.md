# Media File Organizer for Jellyfin

An intelligent, cloud-based media file organizer that automatically scans, matches, and organizes your media files into a clean Jellyfin-compatible folder structure.

## Features

- **Automatic Cloud Scanning** - Scans multiple OneDrive remotes via rclone
- **Smart Filename Parsing** - Extracts title, year, season, episode, quality from messy filenames
- **TMDB Integration** - Matches content with The Movie Database for accurate metadata
- **AI-Powered Fallback** - Uses OpenAI to interpret weird filenames when TMDB fails (optional)
- **Multi-Language Detection** - Supports 20+ languages including Hindi, English, Tamil, Telugu, Korean, Japanese, etc.
- **Jellyfin-Ready Structure** - Organizes files into proper folder hierarchy
- **Quality Management** - Auto-replaces CAM quality with HD when available
- **Docker Ready** - Easy deployment with Docker Compose
- **Safe Operations** - Dry-run mode, rollback support, detailed logging

## Supported Content Types

| Type | Example Output |
|------|----------------|
| Movies | `Movies/Wonka (2023) - Hindi-English/Wonka (2023) - Hindi-English - 1080p.mkv` |
| TV Shows | `TV Shows/Money Heist (2017) - Hindi/Season 01/Money Heist S01E01 - Hindi.mkv` |
| Anime | `Anime/Attack on Titan (2013) - Japanese/Season 01/Attack on Titan S01E01 - Japanese.mkv` |
| K-Drama | `K-Drama/Squid Game (2021) - Korean/Season 01/Squid Game S01E01 - Korean.mkv` |

## Quick Start

### Prerequisites

- Docker & Docker Compose installed
- rclone configured with your cloud remotes
- TMDB API key (free from themoviedb.org)

### Installation

```bash
# Clone the repository
git clone https://github.com/GauravBhatt1/media-organizer.git
cd media-organizer

# Edit docker-compose.yml with your rclone config path
nano docker-compose.yml

# Start the container
sudo docker-compose up -d --build

# View logs
sudo docker-compose logs -f
```

### Configuration

Edit `config.yaml` to customize:

```yaml
# Remotes to scan
scan_remotes:
  - "movies"
  - "tvshows"
  - "anime"
  - "kdrama"

# Scan settings
scan:
  interval_minutes: 2          # How often to scan
  stability_check_seconds: 60  # Wait time before processing
```

## How It Works

```
1. SCAN      -> Discovers new media files in cloud storage
2. PARSE     -> Extracts metadata from filename/folder name
3. MATCH     -> Queries TMDB for accurate title & year
4. ORGANIZE  -> Moves file to proper Jellyfin folder structure
5. TRACK     -> Records in database to prevent duplicates
```

## Architecture

```
+----------------+     +----------------+     +----------------+
|    Scanner     | --> |   TMDB Matcher | --> | Decision Engine|
| (rclone lsjson)|     | (API matching) |     | (quality logic)|
+----------------+     +----------------+     +----------------+
                                                      |
                                                      v
+----------------+     +----------------+     +----------------+
|    Database    | <-- |    Executor    | <-- | Path Generator |
| (SQLite track) |     | (rclone move)  |     | (Jellyfin fmt) |
+----------------+     +----------------+     +----------------+
```

## Supported Languages

Hindi, English, Tamil, Telugu, Malayalam, Kannada, Bengali, Marathi, Punjabi, Gujarati, Korean, Japanese, Chinese, Spanish, French, German, Italian, Portuguese, Russian, Arabic, Thai, Vietnamese, Indonesian

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `TMDB_API_KEY` | Your TMDB API key | Required |
| `OPENAI_API_KEY` | OpenAI API key for AI fallback (optional) | - |
| `SCAN_INTERVAL` | Scan interval in seconds | 3600 |
| `DRY_RUN` | Test mode (no file moves) | false |
| `LOG_LEVEL` | Logging verbosity | INFO |

## AI-Powered Fallback (Optional)

When TMDB can't match a weird filename (like `MAA.2025.1080p.Hindi.DS4K.WEB-DL.mkv`), the AI fallback kicks in:

1. **Heuristic Cleanup** - Removes junk words (HDHub4u, x264, etc.)
2. **AI Interpretation** - Uses GPT-4o-mini to guess the correct movie/show name
3. **Re-search TMDB** - Tries again with the cleaned title

To enable AI fallback:
```bash
export OPENAI_API_KEY="your-api-key-here"
```

Without the API key, only heuristic cleanup is used (still helpful!).

## Docker Compose

```yaml
version: "3.8"

services:
  media-organizer:
    build: .
    container_name: media-organizer
    restart: unless-stopped
    environment:
      - TMDB_API_KEY=your_api_key_here
      - SCAN_INTERVAL=3600
      - DRY_RUN=false
    volumes:
      - /path/to/rclone/config:/root/.config/rclone
      - ./data:/app/data
      - ./logs:/app/logs
```

## Portainer Deployment

1. Go to Portainer > Stacks > Add Stack
2. Name: `media-organizer`
3. Paste the docker-compose.yml content
4. Deploy

## Logs & Monitoring

```bash
# View live logs
sudo docker-compose logs -f

# Check container status
sudo docker ps

# Restart container
sudo docker-compose restart
```

## File Structure After Organization

```
movies:/
├── Movies/
│   ├── Wonka (2023) - Hindi-English/
│   │   └── Wonka (2023) - Hindi-English - 1080p.mkv
│   └── Oppenheimer (2023) - English/
│       └── Oppenheimer (2023) - English - 2160p.mkv

tvshows:/
├── TV Shows/
│   └── Money Heist (2017) - Hindi/
│       ├── Season 01/
│       │   ├── Money Heist S01E01 - Hindi.mkv
│       │   └── Money Heist S01E02 - Hindi.mkv
│       └── Season 02/
│           └── Money Heist S02E01 - Hindi.mkv
```

## Contributing

Feel free to open issues or submit pull requests!

## License

MIT License - Feel free to use and modify!

---

**Built with Python, rclone, TMDB API, and Docker**
