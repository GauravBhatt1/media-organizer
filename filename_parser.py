"""
Filename Parser for Media File Organizer
Extracts title, year, season, episode, and quality from filenames
"""

import re
import logging
from typing import Optional, Tuple, Dict, Any
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ParsedFilename:
    """Parsed information from a media filename."""
    original_filename: str
    title: str
    year: Optional[int]
    season: Optional[int]
    episode: Optional[int]
    quality: str
    is_series: bool
    extension: str
    
    # Additional detected info
    is_multi_episode: bool = False
    episode_end: Optional[int] = None
    release_group: Optional[str] = None


class FilenameParser:
    """
    Parser for media filenames.
    Handles various naming conventions from scene releases, web-dl, etc.
    """
    
    # Quality patterns (order matters - check higher quality first)
    QUALITY_PATTERNS = [
        (r'2160p|4k|uhd', '2160p'),
        (r'1080p|1080i|fullhd|fhd', '1080p'),
        (r'720p|hd(?!ts|tc|cam)', '720p'),
        (r'hdts|hd-ts|hd\.ts|hdtelesync', 'HDTS'),
        (r'hdtc|hd-tc|hd\.tc|hdtelecine', 'HDTC'),
        (r'cam(?:rip)?|hdcam', 'CAM'),
        (r'dvdscr|dvd-scr|screener', 'DVDScr'),
        (r'dvdrip|dvd-rip|dvd', 'DVDRip'),
        (r'bluray|blu-ray|bdrip|brrip', '1080p'),  # Default BluRay to 1080p
        (r'webrip|web-rip', '720p'),  # Default WebRip to 720p
        (r'webdl|web-dl|web\.dl', '1080p'),  # Default Web-DL to 1080p
    ]
    
    # Season/Episode patterns
    SEASON_EPISODE_PATTERNS = [
        # S01E01 or S01E01E02 (multi-episode)
        r'[Ss](\d{1,2})[Ee](\d{1,3})(?:[Ee](\d{1,3}))?',
        # Season 1 Episode 1
        r'[Ss]eason\s*(\d{1,2})\s*[Ee]pisode\s*(\d{1,3})',
        # 1x01 format
        r'(\d{1,2})[xX](\d{1,3})',
        # Part/Episode number only (for single season shows)
        r'[Ee](?:pisode)?\.?\s*(\d{1,3})(?!\d)',
    ]
    
    # Year patterns
    YEAR_PATTERN = r'(?:19|20)\d{2}'
    
    # Common words to remove
    NOISE_WORDS = [
        r'\b(?:extended|directors?\.?cut|unrated|remastered|repack|proper|real)\b',
        r'\b(?:internal|limited|complete|dual\.?audio|multi)\b',
        r'\b(?:hindi|english|tamil|telugu|dubbed|subbed)\b',
        r'\b(?:x264|x265|hevc|h\.?264|h\.?265|avc|xvid|divx)\b',
        r'\b(?:aac|ac3|dts|dd5\.?1|atmos|truehd|flac|mp3)\b',
        r'\b(?:10bit|hdr|sdr|dv|dolby\.?vision)\b',
        r'\b(?:amzn|nf|netflix|hmax|dsnp|atvp|hulu|pcok)\b',
        r'\b(?:web-?dl|webrip|bluray|bdrip|brrip|dvdrip)\b',
        r'\b(?:hdrip|hdtv|pdtv|dsr)\b',
        r'\b(?:esub|esubs|msub|msubs)\b',
        r'\[\w+\]',  # [SubGroup] style tags
        r'\(\w+\)',  # (Group) style tags at end
    ]
    
    # Release group pattern (at end of filename)
    RELEASE_GROUP_PATTERN = r'[-\s]([A-Za-z0-9]+)(?:\.[a-z]{2,4})?$'
    
    def __init__(self, video_extensions: list = None):
        """
        Initialize parser.
        
        Args:
            video_extensions: List of valid video extensions
        """
        self.video_extensions = video_extensions or [
            '.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v'
        ]
    
    def parse(self, filename: str) -> ParsedFilename:
        """
        Parse a filename and extract media information.
        
        Args:
            filename: The filename to parse (can include path)
        
        Returns:
            ParsedFilename object with extracted information
        """
        # Get just the filename without path
        path = Path(filename)
        name = path.stem
        extension = path.suffix.lower()
        original = name
        
        logger.debug(f"Parsing filename: {name}")
        
        # Extract quality first (before cleaning)
        quality = self._extract_quality(name)
        
        # Extract season/episode info
        season, episode, episode_end, is_multi = self._extract_season_episode(name)
        is_series = season is not None or episode is not None
        
        # Extract year
        year = self._extract_year(name, is_series)
        
        # Extract release group
        release_group = self._extract_release_group(name)
        
        # Clean and extract title
        title = self._extract_title(name, year, season, episode)
        
        result = ParsedFilename(
            original_filename=original,
            title=title,
            year=year,
            season=season if season else (1 if episode and not season else None),
            episode=episode,
            quality=quality,
            is_series=is_series,
            extension=extension,
            is_multi_episode=is_multi,
            episode_end=episode_end,
            release_group=release_group
        )
        
        logger.debug(f"Parsed result: title='{title}', year={year}, "
                    f"S{season}E{episode}, quality={quality}, is_series={is_series}")
        
        return result
    
    def _extract_quality(self, name: str) -> str:
        """Extract quality from filename."""
        name_lower = name.lower()
        
        for pattern, quality in self.QUALITY_PATTERNS:
            if re.search(pattern, name_lower):
                logger.debug(f"Detected quality: {quality}")
                return quality
        
        # Default quality if none detected
        return "Unknown"
    
    def _extract_season_episode(self, name: str) -> Tuple[Optional[int], Optional[int], Optional[int], bool]:
        """
        Extract season and episode numbers.
        
        Returns:
            Tuple of (season, episode, episode_end, is_multi_episode)
        """
        # Try S01E01 format first (most common)
        match = re.search(r'[Ss](\d{1,2})[Ee](\d{1,3})(?:[Ee-](\d{1,3}))?', name)
        if match:
            season = int(match.group(1))
            episode = int(match.group(2))
            episode_end = int(match.group(3)) if match.group(3) else None
            return season, episode, episode_end, episode_end is not None
        
        # Try Season X Episode Y format
        match = re.search(r'[Ss]eason\s*(\d{1,2})\s*[Ee]pisode\s*(\d{1,3})', name, re.IGNORECASE)
        if match:
            return int(match.group(1)), int(match.group(2)), None, False
        
        # Try 1x01 format
        match = re.search(r'(\d{1,2})[xX](\d{1,3})', name)
        if match:
            return int(match.group(1)), int(match.group(2)), None, False
        
        # Try Episode XX format (assume season 1)
        match = re.search(r'[Ee](?:pisode)?\.?\s*(\d{1,3})(?!\d)', name)
        if match:
            return 1, int(match.group(1)), None, False
        
        # Try - XX - format for anime (episode only)
        match = re.search(r'\s-\s(\d{1,3})\s', name)
        if match:
            return 1, int(match.group(1)), None, False
        
        return None, None, None, False
    
    def _extract_year(self, name: str, is_series: bool) -> Optional[int]:
        """Extract year from filename."""
        # Find all years in filename
        years = re.findall(self.YEAR_PATTERN, name)
        
        if not years:
            return None
        
        # For series, prefer year at beginning (show premiere year)
        # For movies, prefer year near title
        if len(years) == 1:
            return int(years[0])
        
        # If multiple years, take the first one that's likely the release year
        for year_str in years:
            year = int(year_str)
            if 1950 <= year <= 2030:
                return year
        
        return int(years[0])
    
    def _extract_release_group(self, name: str) -> Optional[str]:
        """Extract release group from filename."""
        match = re.search(self.RELEASE_GROUP_PATTERN, name)
        if match:
            group = match.group(1)
            # Filter out common false positives
            if group.lower() not in ['mkv', 'mp4', 'avi', 'mov', 'wmv']:
                return group
        return None
    
    def _extract_title(self, name: str, year: Optional[int], 
                       season: Optional[int], episode: Optional[int]) -> str:
        """Extract and clean the title from filename."""
        title = name
        
        # Remove file extension if present
        title = re.sub(r'\.[a-z0-9]{2,4}$', '', title, flags=re.IGNORECASE)
        
        # Remove quality indicators
        for pattern, _ in self.QUALITY_PATTERNS:
            title = re.sub(pattern, '', title, flags=re.IGNORECASE)
        
        # Remove season/episode patterns
        title = re.sub(r'[Ss]\d{1,2}[Ee]\d{1,3}(?:[Ee-]\d{1,3})?', '', title)
        title = re.sub(r'[Ss]eason\s*\d{1,2}\s*[Ee]pisode\s*\d{1,3}', '', title, flags=re.IGNORECASE)
        title = re.sub(r'\d{1,2}[xX]\d{1,3}', '', title)
        title = re.sub(r'\s-\s\d{1,3}\s', ' ', title)
        
        # Remove year (but save it)
        if year:
            title = re.sub(rf'\(?{year}\)?', '', title)
        
        # Remove noise words
        for pattern in self.NOISE_WORDS:
            title = re.sub(pattern, '', title, flags=re.IGNORECASE)
        
        # Replace common separators with spaces
        title = re.sub(r'[._\-]+', ' ', title)
        
        # Remove brackets and their contents
        title = re.sub(r'\[.*?\]', '', title)
        title = re.sub(r'\(.*?\)', '', title)
        
        # Clean up multiple spaces and trim
        title = re.sub(r'\s+', ' ', title).strip()
        
        # Title case
        title = self._title_case(title)
        
        return title
    
    def _title_case(self, title: str) -> str:
        """Convert to proper title case."""
        # Words that should stay lowercase
        minor_words = {'a', 'an', 'the', 'and', 'but', 'or', 'nor', 'for', 
                       'yet', 'so', 'at', 'by', 'in', 'of', 'on', 'to', 'up'}
        
        words = title.lower().split()
        result = []
        
        for i, word in enumerate(words):
            if i == 0 or word not in minor_words:
                result.append(word.capitalize())
            else:
                result.append(word)
        
        return ' '.join(result)
    
    def is_video_file(self, filename: str) -> bool:
        """Check if a filename is a video file."""
        ext = Path(filename).suffix.lower()
        return ext in self.video_extensions
