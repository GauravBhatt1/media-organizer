"""
Configuration Loader for Media File Organizer
Loads config from YAML and environment variables
"""

import os
import logging
from pathlib import Path
from typing import Dict, Any, List

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


class Config:
    def __init__(self, config_path: str = "config.yaml"):
        # Load environment variables
        load_dotenv()
        
        self.config_path = config_path
        self._config: Dict[str, Any] = {}
        self._load_config()
        self._validate_config()
    
    def _load_config(self):
        """Load configuration from YAML file."""
        config_file = Path(self.config_path)
        
        if not config_file.exists():
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")
        
        with open(config_file, 'r') as f:
            self._config = yaml.safe_load(f)
        
        logger.info(f"Configuration loaded from {self.config_path}")
    
    def _validate_config(self):
        """Validate required configuration."""
        # Check TMDB API key
        if not self.tmdb_api_key:
            raise ValueError("TMDB_API_KEY environment variable is required")
        
        # Check remotes
        if not self.scan_remotes:
            raise ValueError("At least one remote must be configured in scan_remotes")
        
        logger.info("Configuration validated successfully")
    
    # ==================== Properties ====================
    
    @property
    def tmdb_api_key(self) -> str:
        """Get TMDB API key from environment."""
        return os.getenv("TMDB_API_KEY", "")
    
    @property
    def scan_remotes(self) -> List[str]:
        """Get list of remotes to scan."""
        return self._config.get("scan_remotes", [])
    
    @property
    def scan_interval_minutes(self) -> int:
        """Get scan interval in minutes. Can be overridden by SCAN_INTERVAL env var (in seconds)."""
        env_interval = os.getenv("SCAN_INTERVAL")
        if env_interval:
            return int(env_interval) // 60  # Convert seconds to minutes
        return self._config.get("scan", {}).get("interval_minutes", 5)
    
    @property
    def stability_check_seconds(self) -> int:
        """Get stability check duration in seconds."""
        return self._config.get("scan", {}).get("stability_check_seconds", 120)
    
    @property
    def run_on_startup(self) -> bool:
        """Whether to run scan immediately on startup."""
        return self._config.get("scan", {}).get("run_on_startup", True)
    
    @property
    def quality_priority(self) -> List[str]:
        """Get quality priority list (lowest to highest)."""
        return self._config.get("quality", {}).get("priority", [
            "CAM", "HDTS", "HDTC", "720p", "1080p", "2160p", "4K"
        ])
    
    @property
    def auto_replace_quality(self) -> bool:
        """Whether to auto-replace lower quality with higher."""
        return self._config.get("quality", {}).get("auto_replace", True)
    
    @property
    def cam_replacement_threshold(self) -> str:
        """Minimum quality to replace CAM."""
        return self._config.get("quality", {}).get("cam_replacement_threshold", "720p")
    
    @property
    def tmdb_language(self) -> str:
        """TMDB language for metadata."""
        return self._config.get("tmdb", {}).get("language", "en-US")
    
    @property
    def include_adult(self) -> bool:
        """Whether to include adult content in TMDB searches."""
        return self._config.get("tmdb", {}).get("include_adult", False)
    
    @property
    def video_extensions(self) -> List[str]:
        """List of video file extensions to process."""
        return self._config.get("video_extensions", [
            ".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v"
        ])
    
    @property
    def logging_level(self) -> str:
        """Logging level. Can be overridden by LOG_LEVEL env var."""
        return os.getenv("LOG_LEVEL", self._config.get("logging", {}).get("level", "INFO"))
    
    @property
    def log_file(self) -> str:
        """Log file path. Can be overridden by LOG_FILE env var."""
        log_dir = os.getenv("ORGANIZER_LOG_DIR", "")
        default_file = self._config.get("logging", {}).get("file", "organizer.log")
        if log_dir:
            return os.path.join(log_dir, default_file)
        return default_file
    
    @property
    def log_max_size_mb(self) -> int:
        """Maximum log file size in MB."""
        return self._config.get("logging", {}).get("max_size_mb", 10)
    
    @property
    def log_backup_count(self) -> int:
        """Number of log file backups to keep."""
        return self._config.get("logging", {}).get("backup_count", 5)
    
    @property
    def database_path(self) -> str:
        """Database file path. Can be overridden by ORGANIZER_DB env var."""
        return os.getenv("ORGANIZER_DB", 
                        os.getenv("DATABASE_PATH",
                        self._config.get("database", {}).get("path", "organizer.db")))
    
    # ==================== Folder Structure Templates ====================
    
    def get_folder_template(self, content_type: str) -> str:
        """Get folder template for content type."""
        templates = self._config.get("folder_structure", {})
        return templates.get(content_type, templates.get("movie", "{title} ({year})"))
    
    def get_file_template(self, content_type: str) -> str:
        """Get file naming template for content type."""
        templates = self._config.get("folder_structure", {})
        key = f"{content_type}_file"
        return templates.get(key, "{title} ({year})")
    
    # ==================== Remote Configuration ====================
    
    def get_remote_type(self, remote_name: str) -> str:
        """Get content type for a remote (movie, tvshow, anime, kdrama)."""
        remotes_config = self._config.get("remotes", {})
        
        for category, remotes in remotes_config.items():
            if isinstance(remotes, list):
                for remote in remotes:
                    if remote.get("name") == remote_name:
                        return remote.get("type", "movie")
        
        # Default to movie if not found
        return "movie"
    
    def get_quality_index(self, quality: str) -> int:
        """Get priority index for a quality (higher = better)."""
        quality_upper = quality.upper()
        priority = [q.upper() for q in self.quality_priority]
        
        try:
            return priority.index(quality_upper)
        except ValueError:
            return -1  # Unknown quality
    
    def is_quality_better(self, new_quality: str, existing_quality: str) -> bool:
        """Check if new quality is better than existing."""
        new_idx = self.get_quality_index(new_quality)
        existing_idx = self.get_quality_index(existing_quality)
        return new_idx > existing_idx
    
    def should_replace_cam(self, new_quality: str) -> bool:
        """Check if new quality should replace CAM."""
        threshold_idx = self.get_quality_index(self.cam_replacement_threshold)
        new_idx = self.get_quality_index(new_quality)
        return new_idx >= threshold_idx
