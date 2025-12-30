"""
Decision Engine for Media File Organizer
Determines folder structure, quality replacement, and move operations
"""

import logging
import re
from typing import Optional, Tuple, Dict, Any
from dataclasses import dataclass
from pathlib import PurePosixPath

from config_loader import Config
from database import Database
from filename_parser import FilenameParser, ParsedFilename
from tmdb_matcher import TMDBMatcher, TMDBMatch

logger = logging.getLogger(__name__)


@dataclass
class MoveDecision:
    """Represents a decision about how to handle a file."""
    action: str  # 'move', 'replace', 'skip', 'error'
    source_remote: str
    source_path: str
    destination_remote: str
    destination_path: str
    
    # Metadata
    tmdb_id: Optional[int]
    tmdb_type: Optional[str]
    title: str
    year: Optional[int]
    season: Optional[int]
    episode: Optional[int]
    quality: str
    content_type: str
    
    # For replacement actions
    file_to_delete: Optional[str] = None
    delete_remote: Optional[str] = None
    
    # Error info
    error_message: Optional[str] = None


class DecisionEngine:
    """
    Makes decisions about how to organize media files.
    Handles folder structure, quality comparison, and replacement logic.
    """
    
    def __init__(self, config: Config, db: Database, 
                 parser: FilenameParser, tmdb: TMDBMatcher):
        """
        Initialize decision engine.
        
        Args:
            config: Configuration object
            db: Database instance
            parser: FilenameParser instance
            tmdb: TMDBMatcher instance
        """
        self.config = config
        self.db = db
        self.parser = parser
        self.tmdb = tmdb
    
    def decide(self, remote: str, path: str) -> MoveDecision:
        """
        Decide what to do with a file.
        
        Args:
            remote: Source remote name
            path: File path on remote
        
        Returns:
            MoveDecision object with action and destination
        """
        filename = PurePosixPath(path).name
        logger.info(f"Making decision for: {remote}:{path}")
        
        # Parse filename
        parsed = self.parser.parse(filename)
        
        if parsed.quality == "Unknown":
            logger.debug(f"Unknown quality for {filename}, defaulting to 1080p")
        
        # Get content type from remote
        content_type = self.config.get_remote_type(remote)
        
        # Match with TMDB
        tmdb_match = self.tmdb.match(parsed, content_type)
        
        if not tmdb_match:
            return MoveDecision(
                action='error',
                source_remote=remote,
                source_path=path,
                destination_remote=remote,
                destination_path=path,
                tmdb_id=None,
                tmdb_type=None,
                title=parsed.title,
                year=parsed.year,
                season=parsed.season,
                episode=parsed.episode,
                quality=parsed.quality,
                content_type=content_type,
                error_message=f"No TMDB match found for: {parsed.title}"
            )
        
        # Use TMDB data for accurate title and year
        title = self._clean_title(tmdb_match.title)
        year = tmdb_match.year or parsed.year
        
        # Determine content type from TMDB
        is_series = tmdb_match.tmdb_type == 'tv'
        if is_series:
            content_type = content_type if content_type in ('anime', 'kdrama') else 'tvshow'
        else:
            content_type = 'movie'
        
        # Generate destination path
        destination_path = self._generate_destination_path(
            title=title,
            year=year,
            season=parsed.season,
            episode=parsed.episode,
            quality=parsed.quality,
            extension=parsed.extension,
            content_type=content_type
        )
        
        # Check for quality replacement
        action, file_to_delete, delete_remote = self._check_quality_replacement(
            tmdb_id=tmdb_match.tmdb_id,
            tmdb_type=tmdb_match.tmdb_type,
            season=parsed.season,
            episode=parsed.episode,
            new_quality=parsed.quality,
            destination_path=destination_path,
            remote=remote
        )
        
        return MoveDecision(
            action=action,
            source_remote=remote,
            source_path=path,
            destination_remote=remote,  # Stay in same remote
            destination_path=destination_path,
            tmdb_id=tmdb_match.tmdb_id,
            tmdb_type=tmdb_match.tmdb_type,
            title=title,
            year=year,
            season=parsed.season,
            episode=parsed.episode,
            quality=parsed.quality,
            content_type=content_type,
            file_to_delete=file_to_delete,
            delete_remote=delete_remote
        )
    
    def _clean_title(self, title: str) -> str:
        """Clean title for use in folder/file names."""
        # Remove characters not allowed in filenames
        cleaned = re.sub(r'[<>:"/\\|?*]', '', title)
        # Replace multiple spaces with single space
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return cleaned
    
    def _generate_destination_path(self, title: str, year: Optional[int],
                                    season: Optional[int], episode: Optional[int],
                                    quality: str, extension: str,
                                    content_type: str) -> str:
        """
        Generate Jellyfin-compatible destination path.
        
        Movies: Movies/Movie Name (Year)/Movie Name (Year) - Quality.ext
        Series: TV Shows/Show Name (Year)/Season XX/Show Name SXXEXX.ext
        """
        year_str = str(year) if year else "Unknown"
        
        # Get destination folder from config (e.g., "Movies", "TV Shows")
        dest_folder = self.config.get_destination_folder(content_type)
        
        if content_type == 'movie':
            # Movies: Movies/Movie Name (Year)/Movie Name (Year) - Quality.ext
            movie_folder = f"{title} ({year_str})"
            
            # Add quality to filename
            if quality and quality != "Unknown":
                filename = f"{title} ({year_str}) - {quality}{extension}"
            else:
                filename = f"{title} ({year_str}){extension}"
            
            if dest_folder:
                return f"{dest_folder}/{movie_folder}/{filename}"
            return f"{movie_folder}/{filename}"
        
        else:
            # TV Shows/Anime/K-Drama: TV Shows/Show Name (Year)/Season XX/Show Name SXXEXX.ext
            season_num = season if season else 1
            episode_num = episode if episode else 1
            
            show_folder = f"{title} ({year_str})"
            season_folder = f"Season {season_num:02d}"
            filename = f"{title} S{season_num:02d}E{episode_num:02d}{extension}"
            
            if dest_folder:
                return f"{dest_folder}/{show_folder}/{season_folder}/{filename}"
            return f"{show_folder}/{season_folder}/{filename}"
    
    def _check_quality_replacement(self, tmdb_id: int, tmdb_type: str,
                                    season: Optional[int], episode: Optional[int],
                                    new_quality: str, destination_path: str,
                                    remote: str) -> Tuple[str, Optional[str], Optional[str]]:
        """
        Check if this file should replace an existing lower-quality version.
        
        Returns:
            Tuple of (action, file_to_delete, delete_remote)
        """
        if not self.config.auto_replace_quality:
            return 'move', None, None
        
        # Check for existing quality entry
        existing = self.db.get_existing_quality(
            tmdb_id=tmdb_id,
            tmdb_type=tmdb_type,
            season=season,
            episode=episode
        )
        
        if not existing:
            # No existing file - just move
            return 'move', None, None
        
        existing_quality = existing.get('quality', 'Unknown')
        existing_path = existing.get('file_path')
        existing_remote = existing.get('remote')
        
        # Compare qualities
        if self.config.is_quality_better(new_quality, existing_quality):
            # New quality is better - replace
            logger.info(f"Quality upgrade: {existing_quality} -> {new_quality}")
            
            # Special handling for CAM replacement
            if existing_quality.upper() == 'CAM':
                if self.config.should_replace_cam(new_quality):
                    return 'replace', existing_path, existing_remote
                else:
                    logger.info(f"New quality {new_quality} doesn't meet CAM replacement threshold")
                    return 'skip', None, None
            
            return 'replace', existing_path, existing_remote
        
        elif new_quality == existing_quality:
            # Same quality - skip
            logger.info(f"Same quality exists: {existing_quality}")
            return 'skip', None, None
        
        else:
            # New quality is worse - skip
            logger.info(f"Existing quality is better: {existing_quality} > {new_quality}")
            return 'skip', None, None
    
    def decide_for_folder(self, remote: str, folder: str, 
                          files: list) -> list:
        """
        Make decisions for all files in a folder (series).
        
        Args:
            remote: Remote name
            folder: Folder path
            files: List of files in folder
        
        Returns:
            List of MoveDecision objects
        """
        decisions = []
        
        for file in files:
            if hasattr(file, 'path'):
                path = file.path
            else:
                path = file
            
            decision = self.decide(remote, path)
            decisions.append(decision)
        
        return decisions
