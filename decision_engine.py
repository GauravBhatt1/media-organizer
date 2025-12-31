"""
Decision Engine for Media File Organizer
Determines folder structure, quality replacement, and move operations

Flow: TMDB first -> Web Search correction -> Retry TMDB -> AI fallback (optional)
"""

import logging
import os
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
    
    Decision Flow:
    1. Parse filename and extract metadata
    2. Search TMDB with parsed title
    3. If TMDB fails, use web search to verify/correct title
    4. Retry TMDB with corrected title
    5. If still fails and AI enabled, use AI as last resort
    """
    
    # Minimum confidence threshold for TMDB matches
    TMDB_CONFIDENCE_THRESHOLD = 0.6
    
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
        
        # Lazy-load web searcher (avoid import if not needed)
        self._web_searcher = None
        
        # AI is optional and disabled by default
        self.use_ai_fallback = os.environ.get('USE_AI_FALLBACK', 'false').lower() == 'true'
    
    @property
    def web_searcher(self):
        """Lazy-load web searcher."""
        if self._web_searcher is None:
            try:
                from web_search import WebSearcher
                self._web_searcher = WebSearcher()
                logger.debug("Web searcher initialized")
            except Exception as e:
                logger.warning(f"Web searcher not available: {e}")
                self._web_searcher = False  # Mark as unavailable
        return self._web_searcher if self._web_searcher else None
    
    def decide(self, remote: str, path: str) -> MoveDecision:
        """
        Decide what to do with a file.
        Uses TMDB-first approach with web search fallback.
        
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
        
        # Get content type hint from remote config
        content_type = self.config.get_remote_type(remote)
        
        # STEP 1: Parse filename
        parsed = self.parser.parse(filename)
        
        # If filename parsing gave poor results, try parent folder name
        if self._is_generic_parse(parsed):
            if parent_folder and parent_folder not in ('.', 'files'):
                logger.debug(f"Filename generic, trying folder name: {parent_folder}")
                folder_parsed = self.parser.parse(parent_folder)
                parsed = self._merge_parsed(parsed, folder_parsed)
        
        logger.debug(f"Parsed: title='{parsed.title}', year={parsed.year}, "
                    f"S{parsed.season}E{parsed.episode}, quality={parsed.quality}")
        
        # STEP 2: Try TMDB first
        tmdb_match = self._try_tmdb_match(parsed, content_type, parent_folder)
        
        if tmdb_match and tmdb_match.confidence >= self.TMDB_CONFIDENCE_THRESHOLD:
            logger.info(f"TMDB match found: {tmdb_match.title} ({tmdb_match.year}) "
                       f"[confidence: {tmdb_match.confidence:.0%}]")
            return self._build_decision_from_tmdb(
                remote, path, parsed, tmdb_match, content_type
            )
        
        # STEP 3: Use web search to verify/correct title
        corrected_title = None
        corrected_year = None
        corrected_type = None
        
        if self.web_searcher:
            logger.info(f"TMDB match insufficient, trying web search for: {parsed.title}")
            
            web_result = self.web_searcher.search_title(
                title=parsed.title,
                year=parsed.year,
                media_type=self._get_media_type_hint(content_type, parsed)
            )
            
            if web_result and web_result.get('verified_title'):
                corrected_title = web_result['verified_title']
                corrected_year = web_result.get('year')
                corrected_type = web_result.get('media_type')
                
                logger.info(f"Web search corrected title: '{parsed.title}' -> '{corrected_title}'")
        
        # STEP 4: Retry TMDB with corrected title
        if corrected_title:
            corrected_parsed = ParsedFilename(
                original_filename=parsed.original_filename,
                title=corrected_title,
                year=corrected_year or parsed.year,
                season=parsed.season,
                episode=parsed.episode,
                quality=parsed.quality,
                is_series=corrected_type == 'tv' or parsed.is_series,
                extension=parsed.extension,
                languages=parsed.languages
            )
            
            # Update content type if web search found it
            if corrected_type:
                if corrected_type == 'tv':
                    content_type = content_type if content_type in ('anime', 'kdrama') else 'tvshow'
                elif corrected_type == 'movie':
                    content_type = 'movie'
            
            tmdb_match = self._try_tmdb_match(corrected_parsed, content_type, parent_folder)
            
            if tmdb_match and tmdb_match.confidence >= self.TMDB_CONFIDENCE_THRESHOLD:
                logger.info(f"TMDB match after web correction: {tmdb_match.title} ({tmdb_match.year})")
                return self._build_decision_from_tmdb(
                    remote, path, corrected_parsed, tmdb_match, content_type
                )
        
        # STEP 5: AI fallback (optional, disabled by default)
        if self.use_ai_fallback:
            try:
                ai_decision = self._try_ai_fallback(remote, path, filename, parent_folder, content_type)
                if ai_decision:
                    return ai_decision
            except Exception as e:
                logger.warning(f"AI fallback failed: {e}")
        
        # STEP 6: No match found - return error
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
    
    def _try_tmdb_match(self, parsed: ParsedFilename, content_type: str, 
                        folder_name: str = "") -> Optional[TMDBMatch]:
        """Try to match with TMDB."""
        try:
            return self.tmdb.match(parsed, content_type, folder_name=folder_name)
        except Exception as e:
            logger.warning(f"TMDB match error: {e}")
            return None
    
    def _get_media_type_hint(self, content_type: str, parsed: ParsedFilename) -> Optional[str]:
        """Get media type hint for web search."""
        if content_type == 'movie':
            return 'movie'
        elif content_type in ('tvshow', 'anime', 'kdrama'):
            return 'tv'
        elif parsed.is_series or parsed.season or parsed.episode:
            return 'tv'
        return None
    
    def _build_decision_from_tmdb(self, remote: str, path: str, 
                                   parsed: ParsedFilename, tmdb_match: TMDBMatch,
                                   content_type: str) -> MoveDecision:
        """Build MoveDecision from TMDB match."""
        path_obj = PurePosixPath(path)
        
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
    
    def _try_ai_fallback(self, remote: str, path: str, filename: str,
                         parent_folder: str, content_type: str) -> Optional[MoveDecision]:
        """Try AI orchestrator as last resort (optional)."""
        try:
            from ai_orchestrator import get_orchestrator, AIDecision
            
            orchestrator = get_orchestrator()
            if not orchestrator.client:
                logger.debug("AI not available (no API key)")
                return None
            
            ai_decision = orchestrator.analyze(filename, parent_folder, content_type)
            
            if ai_decision.confidence >= 0.7:
                logger.info(f"AI decision (confidence {ai_decision.confidence:.0%}): {ai_decision.title}")
                
                # Build destination path from AI decision
                destination_path = f"{ai_decision.destination_folder}/{ai_decision.destination_filename}"
                
                # Try TMDB lookup for quality replacement tracking
                tmdb_id = None
                tmdb_type = None
                path_obj = PurePosixPath(path)
                
                try:
                    parsed_for_tmdb = ParsedFilename(
                        original_filename=filename,
                        title=ai_decision.title,
                        year=ai_decision.year,
                        season=ai_decision.season,
                        episode=ai_decision.episode,
                        quality=ai_decision.quality,
                        is_series=ai_decision.category in ('tvshow', 'anime', 'kdrama'),
                        extension=path_obj.suffix,
                        languages=ai_decision.languages
                    )
                    tmdb_match = self.tmdb.match(parsed_for_tmdb, ai_decision.category)
                    if tmdb_match and tmdb_match.confidence >= 0.5:
                        tmdb_id = tmdb_match.tmdb_id
                        tmdb_type = tmdb_match.tmdb_type
                except Exception as e:
                    logger.debug(f"TMDB lookup for AI decision failed: {e}")
                
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
        except ImportError:
            logger.debug("AI orchestrator not available")
        except Exception as e:
            logger.warning(f"AI fallback error: {e}")
        
        return None
    
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
