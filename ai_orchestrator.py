"""
AI Orchestrator - Central Brain for Media File Organization
This module acts as the primary decision maker for categorizing,
naming, and organizing media files using AI.
"""

import os
import re
import json
import logging
import hashlib
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, asdict
from pathlib import Path

logger = logging.getLogger(__name__)

# Try to import OpenAI (works for both OpenAI and Groq)
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    logger.warning("OpenAI library not installed. AI orchestration will use heuristics only.")

# AI Provider constants
AI_PROVIDER_GROQ = "groq"
AI_PROVIDER_OPENAI = "openai"
AI_PROVIDER_NONE = "none"


@dataclass
class AIDecision:
    """Structured AI decision for a media file."""
    category: str  # 'movie', 'tvshow', 'anime', 'kdrama'
    title: str  # Clean, canonical title
    year: Optional[int]
    season: Optional[int]
    episode: Optional[int]
    languages: List[str]  # ['Hindi', 'English']
    quality: str  # '1080p', '720p', '4K', 'CAM'
    destination_folder: str  # e.g., 'Movies/Wonka (2023) - Hindi-English'
    destination_filename: str  # e.g., 'Wonka (2023) - Hindi-English - 1080p.mkv'
    confidence: float  # 0.0 to 1.0
    rationale: str  # Why AI made this decision
    method: str  # 'ai', 'heuristic', 'fallback'


class AICache:
    """Simple file-based cache for AI responses to avoid re-billing."""
    
    def __init__(self, cache_dir: str = "data/ai_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_cache_key(self, filename: str, folder: str) -> str:
        """Generate cache key from filename and folder."""
        content = f"{filename}|{folder}"
        return hashlib.md5(content.encode()).hexdigest()
    
    def get(self, filename: str, folder: str) -> Optional[Dict]:
        """Get cached AI response."""
        key = self._get_cache_key(filename, folder)
        cache_file = self.cache_dir / f"{key}.json"
        
        if cache_file.exists():
            try:
                with open(cache_file, 'r') as f:
                    return json.load(f)
            except Exception:
                pass
        return None
    
    def set(self, filename: str, folder: str, decision: Dict):
        """Cache AI response."""
        key = self._get_cache_key(filename, folder)
        cache_file = self.cache_dir / f"{key}.json"
        
        try:
            with open(cache_file, 'w') as f:
                json.dump(decision, f)
        except Exception as e:
            logger.warning(f"Failed to cache AI response: {e}")


class AIOrchestrator:
    """
    Central AI brain for media file organization.
    
    This orchestrator:
    1. Understands its purpose (organizing media for Jellyfin)
    2. Analyzes filenames and folder context
    3. Makes intelligent categorization decisions
    4. Determines proper naming and destination paths
    """
    
    SYSTEM_PROMPT = """You are a Media File Organizer AI. Your job is to analyze messy media filenames and organize them for Jellyfin.

YOUR PURPOSE:
- Analyze media filenames (movies, TV shows, anime, K-dramas)
- Extract the correct title, year, season/episode, quality, and languages
- Categorize content correctly
- Generate clean, Jellyfin-compatible folder and file names

REMOTE TYPES (hints about content):
- 'movies' or 'movies1': Likely movies
- 'tvshows': TV series
- 'anime': Japanese animation
- 'kdrama': Korean dramas

LANGUAGE DETECTION:
- Look for language codes: Hindi, Hin, Eng, Tam, Tel, Kor, Jpn, etc.
- Common patterns: "Hindi-English", "Dual Audio", "Multi"
- Default to English if unclear

QUALITY DETECTION:
- Look for: 4K, 2160p, 1080p, 720p, 480p, HDRip, WEB-DL, BluRay, CAM, HDCAM
- CAM/HDCAM = low quality theater recording

OUTPUT RULES:
- Title should be clean, proper case (e.g., "The Dark Knight" not "the.dark.knight")
- Year in parentheses: "Movie Name (2023)"
- Languages joined with hyphen: "Hindi-English"
- For TV: Include season/episode like "S01E05"

EXAMPLES:
Input: "MAA.2025.1080p.Hindi.DS4K.WEB-DL.mkv" from remote "movies"
Output: {"title": "Maa", "year": 2025, "category": "movie", "languages": ["Hindi"], "quality": "1080p"}

Input: "Squid.Game.S02E01.720p.NF.WEB-DL.Korean.mkv" from remote "kdrama"
Output: {"title": "Squid Game", "year": null, "category": "kdrama", "season": 2, "episode": 1, "languages": ["Korean"], "quality": "720p"}"""

    def __init__(self, openai_api_key: Optional[str] = None, groq_api_key: Optional[str] = None):
        """Initialize AI Orchestrator with support for multiple AI providers."""
        self.openai_key = openai_api_key or os.environ.get('OPENAI_API_KEY')
        self.groq_key = groq_api_key or os.environ.get('GROQ_API_KEY')
        self.client = None
        self.provider = AI_PROVIDER_NONE
        self.model = None
        self.cache = AICache()
        
        if not OPENAI_AVAILABLE:
            logger.info("AI Orchestrator running in heuristic-only mode (openai library not installed)")
            return
        
        # Try Groq first (faster and has generous free tier)
        if self.groq_key:
            try:
                self.client = OpenAI(
                    api_key=self.groq_key,
                    base_url="https://api.groq.com/openai/v1"
                )
                self.provider = AI_PROVIDER_GROQ
                self.model = "llama-3.3-70b-versatile"  # Fast and capable
                logger.info("AI Orchestrator initialized with Groq (FREE tier)")
            except Exception as e:
                logger.warning(f"Failed to initialize Groq: {e}")
        
        # Fall back to OpenAI if Groq not available
        if not self.client and self.openai_key:
            try:
                self.client = OpenAI(api_key=self.openai_key)
                self.provider = AI_PROVIDER_OPENAI
                self.model = "gpt-4o-mini"
                logger.info("AI Orchestrator initialized with OpenAI")
            except Exception as e:
                logger.warning(f"Failed to initialize OpenAI: {e}")
        
        if not self.client:
            logger.info("AI Orchestrator running in heuristic-only mode (no API keys provided)")
    
    def _call_ai(self, filename: str, folder: str, remote_type: str) -> Optional[Dict]:
        """Call AI API for file analysis (supports OpenAI and Groq)."""
        if not self.client or not self.model:
            return None
        
        user_prompt = f"""Analyze this media file:

Filename: {filename}
Folder: {folder}
Remote Type: {remote_type}

Return a JSON object with these fields:
- category: "movie", "tvshow", "anime", or "kdrama"
- title: Clean title (proper case, no dots/underscores)
- year: Year as integer or null
- season: Season number or null
- episode: Episode number or null  
- languages: Array of languages detected (e.g., ["Hindi", "English"])
- quality: Video quality (e.g., "1080p", "720p", "4K")
- rationale: Brief explanation of your analysis

Return ONLY valid JSON, no markdown."""

        try:
            logger.debug(f"Calling {self.provider} API with model {self.model}")
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1,
                max_tokens=500
            )
            
            content = response.choices[0].message.content.strip()
            
            # Clean up response (remove markdown if present)
            if content.startswith("```"):
                content = re.sub(r'^```json?\n?', '', content)
                content = re.sub(r'\n?```$', '', content)
            
            return json.loads(content)
            
        except Exception as e:
            logger.error(f"AI API call failed: {e}")
            return None
    
    def _heuristic_analysis(self, filename: str, folder: str, remote_type: str) -> Dict:
        """Fallback heuristic analysis when AI is unavailable."""
        # Remove extension
        name = re.sub(r'\.[a-zA-Z0-9]{2,4}$', '', filename)
        
        # Detect quality
        quality = "Unknown"
        quality_patterns = [
            (r'4K|2160p', '4K'),
            (r'1080p', '1080p'),
            (r'720p', '720p'),
            (r'480p', '480p'),
            (r'CAM|HDCAM|CAMRIP', 'CAM'),
        ]
        for pattern, q in quality_patterns:
            if re.search(pattern, name, re.IGNORECASE):
                quality = q
                break
        
        # Detect year
        year_match = re.search(r'[.\s\(]?(19\d{2}|20\d{2})[.\s\)]?', name)
        year = int(year_match.group(1)) if year_match else None
        
        # Detect season/episode
        season = None
        episode = None
        se_match = re.search(r'S(\d{1,2})E(\d{1,2})', name, re.IGNORECASE)
        if se_match:
            season = int(se_match.group(1))
            episode = int(se_match.group(2))
        
        # Detect languages
        languages = []
        lang_patterns = {
            'Hindi': r'\bHin(?:di)?\b',
            'English': r'\bEng(?:lish)?\b',
            'Tamil': r'\bTam(?:il)?\b',
            'Telugu': r'\bTel(?:ugu)?\b',
            'Korean': r'\bKor(?:ean)?\b',
            'Japanese': r'\bJap(?:anese)?\b',
        }
        for lang, pattern in lang_patterns.items():
            if re.search(pattern, name, re.IGNORECASE):
                languages.append(lang)
        
        if not languages:
            languages = ['English']
        
        # Clean title
        title = name
        # Remove common junk
        junk_patterns = [
            r'\b(HDHub4u|Filmyzilla|1337x|YIFY|RARBG|MkvCinemas)\b',
            r'\b(WEB-?DL|BluRay|HDRip|DVDRip|BRRip)\b',
            r'\b(x264|x265|HEVC|AAC|DD5\.1)\b',
            r'\b(1080p|720p|480p|4K|2160p)\b',
            r'\bS\d{1,2}E\d{1,2}\b',
            r'[.\-_]',
        ]
        for pattern in junk_patterns:
            title = re.sub(pattern, ' ', title, flags=re.IGNORECASE)
        
        # Remove year from title
        if year:
            title = re.sub(str(year), '', title)
        
        title = ' '.join(title.split()).strip()
        title = title.title()
        
        # Determine category
        category = 'movie'
        if remote_type == 'anime':
            category = 'anime'
        elif remote_type == 'kdrama':
            category = 'kdrama'
        elif remote_type == 'tvshows' or season is not None:
            category = 'tvshow'
        
        return {
            'category': category,
            'title': title,
            'year': year,
            'season': season,
            'episode': episode,
            'languages': languages,
            'quality': quality,
            'rationale': 'Analyzed using heuristic patterns'
        }
    
    def _build_destination(self, analysis: Dict, extension: str) -> Tuple[str, str]:
        """Build destination folder and filename from analysis."""
        title = analysis['title']
        year = analysis['year']
        languages = analysis.get('languages', ['English'])
        quality = analysis.get('quality', 'Unknown')
        category = analysis['category']
        season = analysis.get('season')
        episode = analysis.get('episode')
        
        # Language string
        lang_str = '-'.join(languages) if languages else 'English'
        
        # Build base name
        if year:
            base_name = f"{title} ({year}) - {lang_str}"
        else:
            base_name = f"{title} - {lang_str}"
        
        # Determine root folder
        root_folders = {
            'movie': 'Movies',
            'tvshow': 'TV Shows',
            'anime': 'Anime',
            'kdrama': 'K-Drama'
        }
        root = root_folders.get(category, 'Movies')
        
        # Build folder path
        if category in ('tvshow', 'anime', 'kdrama') and season is not None:
            folder = f"{root}/{base_name}/Season {season:02d}"
            if episode is not None:
                filename = f"{base_name} - S{season:02d}E{episode:02d} - {quality}{extension}"
            else:
                filename = f"{base_name} - S{season:02d} - {quality}{extension}"
        else:
            folder = f"{root}/{base_name}"
            filename = f"{base_name} - {quality}{extension}"
        
        return folder, filename
    
    def analyze(self, filename: str, folder: str = "", 
                remote_type: str = "movies") -> AIDecision:
        """
        Analyze a media file and return organization decision.
        
        Args:
            filename: The media filename
            folder: Parent folder name (for context)
            remote_type: Type of remote (movies, tvshows, anime, kdrama)
        
        Returns:
            AIDecision with complete organization plan
        """
        logger.info(f"AI analyzing: {filename} (folder: {folder}, type: {remote_type})")
        
        # Check cache first
        cached = self.cache.get(filename, folder)
        if cached:
            logger.info("Using cached AI decision")
            ext = os.path.splitext(filename)[1]
            dest_folder, dest_filename = self._build_destination(cached, ext)
            return AIDecision(
                category=cached['category'],
                title=cached['title'],
                year=cached.get('year'),
                season=cached.get('season'),
                episode=cached.get('episode'),
                languages=cached.get('languages', ['English']),
                quality=cached.get('quality', 'Unknown'),
                destination_folder=dest_folder,
                destination_filename=dest_filename,
                confidence=0.9,
                rationale=cached.get('rationale', 'From cache'),
                method='cached'
            )
        
        # Get file extension
        ext = os.path.splitext(filename)[1]
        
        # Try AI first
        analysis = None
        method = 'heuristic'
        confidence = 0.6
        
        if self.client:
            analysis = self._call_ai(filename, folder, remote_type)
            if analysis:
                method = 'ai'
                confidence = 0.95
                logger.info(f"AI analysis successful: {analysis.get('title')}")
        
        # Fallback to heuristics
        if not analysis:
            analysis = self._heuristic_analysis(filename, folder, remote_type)
            logger.info(f"Heuristic analysis: {analysis.get('title')}")
        
        # Cache the result
        self.cache.set(filename, folder, analysis)
        
        # Build destination paths
        dest_folder, dest_filename = self._build_destination(analysis, ext)
        
        return AIDecision(
            category=analysis['category'],
            title=analysis['title'],
            year=analysis.get('year'),
            season=analysis.get('season'),
            episode=analysis.get('episode'),
            languages=analysis.get('languages', ['English']),
            quality=analysis.get('quality', 'Unknown'),
            destination_folder=dest_folder,
            destination_filename=dest_filename,
            confidence=confidence,
            rationale=analysis.get('rationale', ''),
            method=method
        )


# Singleton instance
_orchestrator: Optional[AIOrchestrator] = None


def get_orchestrator() -> AIOrchestrator:
    """Get or create the AI orchestrator instance."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = AIOrchestrator()
    return _orchestrator


def analyze_media_file(filename: str, folder: str = "", 
                       remote_type: str = "movies") -> AIDecision:
    """
    Convenience function to analyze a media file.
    
    Args:
        filename: Media filename to analyze
        folder: Parent folder name
        remote_type: Remote type hint
    
    Returns:
        AIDecision with organization plan
    """
    orchestrator = get_orchestrator()
    return orchestrator.analyze(filename, folder, remote_type)


if __name__ == "__main__":
    # Test the orchestrator
    logging.basicConfig(level=logging.INFO)
    
    test_files = [
        ("MAA.2025.1080p.Hindi.DS4K.WEB-DL.mkv", "", "movies"),
        ("Squid.Game.S02E01.720p.Korean-English.WEB-DL.mkv", "Squid Game Season 2", "kdrama"),
        ("Wonka.2023.1080p.Hindi-English.BluRay.x264.mkv", "", "movies"),
        ("[SubsPlease] Solo Leveling - 01 (1080p).mkv", "Solo Leveling", "anime"),
    ]
    
    for filename, folder, remote_type in test_files:
        print(f"\n{'='*60}")
        print(f"Input: {filename}")
        decision = analyze_media_file(filename, folder, remote_type)
        print(f"Category: {decision.category}")
        print(f"Title: {decision.title}")
        print(f"Year: {decision.year}")
        print(f"Languages: {decision.languages}")
        print(f"Quality: {decision.quality}")
        print(f"Destination: {decision.destination_folder}/{decision.destination_filename}")
        print(f"Confidence: {decision.confidence:.0%}")
        print(f"Method: {decision.method}")
