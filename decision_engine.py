"""
Decision Engine for Media File Organizer
Determines folder structure, quality replacement, and move operations
Now with AI-first approach for intelligent decision making
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
from ai_orchestrator import get_orchestrator, AIDecision

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
        Uses AI-first approach, with TMDB fallback.
        
        Args:
            remote: Source remote name
            path: File path on remote
        
        Returns:
            MoveDecision object with action and destination
        """
        path_obj = PurePosixPath(path)
        filename = path_obj.name
        parent_folder = path_obj.parent.name if path_obj.parent.name not in ('.', '') else ""
        
        logger.info(f"Making decision for: {remote}:{path}")
        
        # Get content type hint from remote
        content_type = self.config.get_remote_type(remote)
        
        # STEP 1: Try AI Orchestrator first (primary brain)
        try:
            orchestrator = get_orchestrator()
            ai_decision = orchestrator.analyze(filename, parent_folder, content_type)
            
            # If AI has high confidence, use its decision directly
            if ai_decision.confidence >= 0.7:
                logger.info(f"AI decision (confidence {ai_decision.confidence:.0%}): {ai_decision.title}")
                
                # Build destination path from AI decision
                destination_path = f"{ai_decision.destination_folder}/{ai_decision.destination_filename}"
                
                # For quality replacement, we still need TMDB ID
                # Try quick TMDB lookup with AI's clean title
                tmdb_id = None
                tmdb_type = None
                
                try:
                    parsed_for_tmdb = ParsedFilename(
                        original_filename=filename,
                        title=ai_decision.title,
                        year=ai_decision.year,
                        season=ai_decision.season,
                        episode=ai_decision.episode,
                        quality=ai_decision.quality,
                        source=None,
                        codec=None,
                        audio=None,
                        is_series=ai_decision.category in ('tvshow', 'anime', 'kdrama'),
                        extension=path_obj.suffix,
                        languages=ai_decision.languages
                    )
                    tmdb_match = self.tmdb.match(parsed_for_tmdb, ai_decision.category)
                    if tmdb_match and tmdb_match.confidence >= 0.5:
                        tmdb_id = tmdb_match.tmdb_id
                        tmdb_type = tmdb_match.tmdb_type
                except Exception as e:
                    logger.debug(f"TMDB lookup failed: {e}")
                
                # Check quality replacement
                action = 'move'
                file_to_delete = None
                delete_remote = None
                
                if tmdb_id:
                    action, file_to_delete, delete_remote = self._check_quality_replacement(
                        tmdb_id=tmdb_id,
                        tmdb_type=tmdb_type,
                        season=ai_decision.season,
                        episode=ai_decision.episode,
                        new_quality=ai_decision.quality,
                        destination_path=destination_path,
                        remote=remote
                    )
                
                return MoveDecision(
                    action=action,
                    source_remote=remote,
                    source_path=path,
                    destination_remote=remote,
                    destination_path=destination_path,
                    tmdb_id=tmdb_id,
                    tmdb_type=tmdb_type,
                    title=ai_decision.title,
                    year=ai_decision.year,
                    season=ai_decision.season,
                    episode=ai_decision.episode,
                    quality=ai_decision.quality,
                    content_type=ai_decision.category,
                    file_to_delete=file_to_delete,
                    delete_remote=delete_remote
                )
        except Exception as e:
            logger.warning(f"AI orchestrator failed, falling back to TMDB: {e}")
        
        # STEP 2: Fallback to traditional TMDB-based approach
        return self._decide_with_tmdb(remote, path, filename, parent_folder, content_type)
    
    def _decide_with_tmdb(self, remote: str, path: str, filename: str, 
                          parent_folder: str, content_type: str) -> MoveDecision:
        """Traditional TMDB-based decision making (fallback)."""
        path_obj = PurePosixPath(path)
        
        # Parse filename first
        parsed = self.parser.parse(filename)
        
        # If filename parsing gave poor results, try parent folder name
        if self._is_generic_parse(parsed):
            if parent_folder and parent_folder not in ('.', 'files'):
                logger.debug(f"Filename generic, trying folder name: {parent_folder}")
                folder_parsed = self.parser.parse(parent_folder)
                parsed = self._merge_parsed(parsed, folder_parsed)
        
        if parsed.quality == "Unknown":
            logger.debug(f"Unknown quality for {filename}, defaulting to 1080p")
        
        # Match with TMDB
        tmdb_match = self.tmdb.match(parsed, content_type, folder_name=parent_folder)
        
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
        
        # Get languages
        languages = getattr(parsed, 'languages', [])
        
        # Generate destination path
        destination_path = self._generate_destination_path(
            title=title,
            year=year,
            season=parsed.season,
            episode=parsed.episode,
            quality=parsed.quality,
            extension=parsed.extension,
            content_type=content_type,
            languages=languages
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
            destination_remote=remote,
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
    
    def _is_generic_parse(self, parsed) -> bool:
        """Check if parsed result is too generic (likely just file extension)."""
        generic_names = {'movie', 'video', 'film', 'sample', 'rarbg', 'yify', 'yts'}
        title_lower = parsed.title.lower().strip()
        
        # Too short or generic
        if len(title_lower) <= 3:
            return True
        if title_lower in generic_names:
            return True
        # No year found and title looks like extension or codec
        if not parsed.year and title_lower in {'mkv', 'mp4', 'avi', 'x264', 'x265', 'hevc'}:
            return True
        return False
    
    def _merge_parsed(self, file_parsed, folder_parsed):
        """Merge parsed info from filename and folder, preferring folder for title/year."""
        from filename_parser import ParsedFilename
        
        # Use folder title/year if file title is generic
        title = folder_parsed.title if not self._is_generic_parse(folder_parsed) else file_parsed.title
        year = folder_parsed.year or file_parsed.year
        
        # Prefer file for season/episode (more reliable)
        season = file_parsed.season or folder_parsed.season
        episode = file_parsed.episode or folder_parsed.episode
        
        # Quality: prefer higher quality source, or folder if file unknown
        quality = file_parsed.quality
        if quality == "Unknown" and folder_parsed.quality != "Unknown":
            quality = folder_parsed.quality
        
        # Merge languages from both sources
        languages = list(set(file_parsed.languages + folder_parsed.languages))
        
        return ParsedFilename(
            original_filename=file_parsed.original_filename,
            title=title,
            year=year,
            season=season,
            episode=episode,
            quality=quality,
            is_series=file_parsed.is_series or folder_parsed.is_series,
            extension=file_parsed.extension,
            languages=languages
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
                                    content_type: str, languages: list = None) -> str:
        """
        Generate Jellyfin-compatible destination path.
        
        Movies: Movies/Movie Name (Year)/Movie Name (Year) - Hindi-English - 1080p.ext
        Series: TV Shows/Show Name (Year)/Season XX/Show Name SXXEXX - Hindi.ext
        """
        year_str = str(year) if year else "Unknown"
        languages = languages or []
        
        # Get destination folder from config (e.g., "Movies", "TV Shows")
        dest_folder = self.config.get_destination_folder(content_type)
        
        # Build language string (e.g., "Hindi-English")
        lang_str = "-".join(languages) if languages else ""
        
        if content_type == 'movie':
            # Movies: Movies/Movie Name (Year) - Hindi-English/Movie Name (Year) - Hindi-English - 1080p.ext
            
            # Build folder name with language
            folder_parts = [f"{title} ({year_str})"]
            if lang_str:
                folder_parts.append(lang_str)
            movie_folder = " - ".join(folder_parts)
            
            # Build filename parts
            file_parts = [f"{title} ({year_str})"]
            if lang_str:
                file_parts.append(lang_str)
            if quality and quality != "Unknown":
                file_parts.append(quality)
            
            filename = " - ".join(file_parts) + extension
            
            if dest_folder:
                return f"{dest_folder}/{movie_folder}/{filename}"
            return f"{movie_folder}/{filename}"
        
        else:
            # TV Shows/Anime/K-Drama: TV Shows/Show Name (Year) - Hindi/Season XX/Show Name SXXEXX - Hindi.ext
            season_num = season if season else 1
            episode_num = episode if episode else 1
            
            # Build show folder with language
            folder_parts = [f"{title} ({year_str})"]
            if lang_str:
                folder_parts.append(lang_str)
            show_folder = " - ".join(folder_parts)
            
            season_folder = f"Season {season_num:02d}"
            
            # Build filename parts
            base = f"{title} S{season_num:02d}E{episode_num:02d}"
            file_parts = [base]
            if lang_str:
                file_parts.append(lang_str)
            
            filename = " - ".join(file_parts) + extension
            
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
