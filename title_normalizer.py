"""
AI-powered Title Normalizer
Fallback system when TMDB can't match weird filenames
Uses OpenAI API to interpret and normalize movie/show titles
"""

import os
import re
import json
import logging
import hashlib
from typing import Optional, Tuple, Dict
from pathlib import Path

logger = logging.getLogger(__name__)

# Cache file for AI responses (avoid re-billing)
CACHE_FILE = "/app/data/title_cache.json"

# Common stopwords and junk to remove
STOPWORDS = {
    'hdtv', 'hdtc', 'hdrip', 'webrip', 'web-dl', 'webdl', 'bluray', 'brrip',
    'dvdrip', 'dvdscr', 'cam', 'ts', 'tc', 'hd', 'sd', '4k', 'uhd',
    'x264', 'x265', 'hevc', 'avc', 'h264', 'h265', '10bit',
    'aac', 'ac3', 'dts', 'dd5', 'dd2', 'atmos', 'truehd',
    'esub', 'esubs', 'subs', 'sub', 'dual', 'multi',
    'hdhub4u', 'hdHub4u', 'yts', 'yify', 'rarbg', 'ettv', 'eztv',
    'telly', 'vegamovies', 'mkvcinemas', 'filmyzilla', 'moviesverse',
    'mkvcage', 'pahe', 'psa', 'qxr', 'sparks', 'gopisahi',
    'org', 'com', 'tv', 'ms', 'to', 'mx', 'in',
    'proper', 'repack', 'internal', 'extended', 'unrated', 'directors',
    'v2', 'v3', 'hq', 'lq', 'ds4k',
    '224kbps', '320kbps', '128kbps', '5.1', '7.1', '2.0', '1.0',
    'movie', 'movies', 'animation', 'animated'
}

# Language mappings (for detection, not removal from title)
LANGUAGES = {
    'hindi', 'english', 'tamil', 'telugu', 'malayalam', 'kannada',
    'bengali', 'marathi', 'punjabi', 'gujarati', 'korean', 'japanese',
    'chinese', 'spanish', 'french', 'german', 'italian', 'portuguese',
    'russian', 'arabic', 'thai', 'vietnamese', 'indonesian',
    'hin', 'eng', 'tam', 'tel', 'mal', 'kan', 'kor', 'jap', 'chi'
}

# Quality patterns to remove
QUALITY_PATTERNS = [
    r'\d{3,4}p',  # 720p, 1080p, 2160p
    r'\d{3,4}x\d{3,4}',  # 1920x1080
]


class TitleNormalizer:
    def __init__(self, openai_api_key: Optional[str] = None):
        self.openai_api_key = openai_api_key or os.environ.get('OPENAI_API_KEY')
        self.cache = self._load_cache()
        self.ai_enabled = bool(self.openai_api_key)
        
        if self.ai_enabled:
            logger.info("AI title normalization enabled (OpenAI)")
        else:
            logger.info("AI title normalization disabled (no API key)")
    
    def _load_cache(self) -> Dict:
        """Load cached AI responses"""
        try:
            if os.path.exists(CACHE_FILE):
                with open(CACHE_FILE, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load cache: {e}")
        return {}
    
    def _save_cache(self):
        """Save cache to disk"""
        try:
            os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
            with open(CACHE_FILE, 'w') as f:
                json.dump(self.cache, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save cache: {e}")
    
    def _get_cache_key(self, filename: str, folder: str = "") -> str:
        """Generate cache key from filename"""
        combined = f"{folder}|{filename}".lower()
        return hashlib.md5(combined.encode()).hexdigest()
    
    def clean_title_heuristic(self, filename: str, folder_name: str = "") -> Tuple[str, Optional[int]]:
        """
        Clean title using heuristics (no AI)
        Returns: (cleaned_title, year or None)
        """
        # Use folder name if filename is generic
        text = filename
        if folder_name and len(folder_name) > len(filename.split('.')[0]):
            text = folder_name
        
        # Remove extension
        text = re.sub(r'\.(mkv|mp4|avi|mov|wmv|flv|webm|m4v)$', '', text, flags=re.IGNORECASE)
        
        # Replace dots and underscores with spaces
        text = text.replace('.', ' ').replace('_', ' ').replace('-', ' ')
        
        # Extract year (4 digits between 1900-2099)
        year_match = re.search(r'\b(19\d{2}|20\d{2})\b', text)
        year = int(year_match.group(1)) if year_match else None
        
        # Remove year from text for cleaner title
        if year:
            text = re.sub(r'\b' + str(year) + r'\b', '', text)
        
        # Remove quality patterns
        for pattern in QUALITY_PATTERNS:
            text = re.sub(pattern, '', text, flags=re.IGNORECASE)
        
        # Remove stopwords
        words = text.lower().split()
        cleaned_words = []
        for word in words:
            # Skip if it's a stopword or language
            if word.lower() in STOPWORDS or word.lower() in LANGUAGES:
                continue
            # Skip if it's just numbers or very short
            if re.match(r'^\d+$', word) or len(word) < 2:
                continue
            cleaned_words.append(word)
        
        # Reconstruct title
        title = ' '.join(cleaned_words)
        
        # Capitalize properly
        title = title.title()
        
        # Clean up extra spaces
        title = re.sub(r'\s+', ' ', title).strip()
        
        return title, year
    
    def normalize_with_ai(self, filename: str, folder_name: str = "") -> Optional[Tuple[str, Optional[int]]]:
        """
        Use OpenAI to normalize weird filenames
        Returns: (normalized_title, year) or None if failed
        """
        if not self.ai_enabled:
            return None
        
        # Check cache first
        cache_key = self._get_cache_key(filename, folder_name)
        if cache_key in self.cache:
            cached = self.cache[cache_key]
            logger.debug(f"Cache hit for: {filename}")
            return cached.get('title'), cached.get('year')
        
        try:
            import urllib.request
            import urllib.error
            
            # Build prompt
            prompt = f"""Extract the movie or TV show title and year from this messy filename.

Filename: {filename}
Folder: {folder_name if folder_name else 'N/A'}

Rules:
1. Return ONLY the clean movie/show title and year
2. Remove all quality info (1080p, 720p, etc.)
3. Remove all codec info (x264, HEVC, etc.)
4. Remove all source info (WEB-DL, BluRay, etc.)
5. Remove all release group names (HDHub4u, YTS, etc.)
6. Remove language tags but remember the title might be in that language
7. If it's a Bollywood/Indian movie, try to identify the correct Hindi/regional title
8. Return JSON format: {{"title": "Movie Name", "year": 2024}}
9. If year is unknown, use null for year
10. Be smart - use your knowledge of movies to guess the correct title

Example:
Input: "MAA.2025.1080p.Hindi.DS4K.WEB-DL.5.1.x264.mkv"
Output: {{"title": "Maa", "year": 2025}}

Input: "Nikita.Roy.2025.1080p.HDTC.Hindi.ORG.x264.mkv"
Output: {{"title": "Nikita Roy and the Book of Darkness", "year": 2025}}"""

            # Call OpenAI API
            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {self.openai_api_key}'
            }
            
            data = json.dumps({
                'model': 'gpt-4o-mini',
                'messages': [
                    {'role': 'system', 'content': 'You are a movie/TV show title extractor. Return only valid JSON.'},
                    {'role': 'user', 'content': prompt}
                ],
                'temperature': 0.1,
                'max_tokens': 100
            }).encode('utf-8')
            
            req = urllib.request.Request(
                'https://api.openai.com/v1/chat/completions',
                data=data,
                headers=headers,
                method='POST'
            )
            
            with urllib.request.urlopen(req, timeout=10) as response:
                result = json.loads(response.read().decode('utf-8'))
            
            # Parse response
            content = result['choices'][0]['message']['content']
            
            # Extract JSON from response
            json_match = re.search(r'\{[^}]+\}', content)
            if json_match:
                parsed = json.loads(json_match.group())
                title = parsed.get('title', '').strip()
                year = parsed.get('year')
                
                if title:
                    # Cache the result
                    self.cache[cache_key] = {'title': title, 'year': year}
                    self._save_cache()
                    
                    logger.info(f"AI normalized: '{filename}' -> '{title}' ({year})")
                    return title, year
            
        except urllib.error.HTTPError as e:
            logger.warning(f"OpenAI API error: {e.code} - {e.reason}")
        except Exception as e:
            logger.warning(f"AI normalization failed: {e}")
        
        return None
    
    def normalize(self, filename: str, folder_name: str = "") -> Tuple[str, Optional[int], str]:
        """
        Main normalization function - tries heuristics first, then AI
        Returns: (title, year, method_used)
        """
        # First try heuristic cleanup
        heuristic_title, heuristic_year = self.clean_title_heuristic(filename, folder_name)
        
        # If heuristic result looks good (has reasonable length), use it
        if heuristic_title and len(heuristic_title) >= 2:
            # If AI is available, try to improve it
            if self.ai_enabled:
                ai_result = self.normalize_with_ai(filename, folder_name)
                if ai_result and ai_result[0]:
                    return ai_result[0], ai_result[1], "ai"
            
            return heuristic_title, heuristic_year, "heuristic"
        
        # Fallback to AI if heuristic failed
        if self.ai_enabled:
            ai_result = self.normalize_with_ai(filename, folder_name)
            if ai_result and ai_result[0]:
                return ai_result[0], ai_result[1], "ai"
        
        # Last resort - return cleaned filename
        return heuristic_title or filename.split('.')[0].title(), heuristic_year, "fallback"


# Singleton instance
_normalizer = None

def get_normalizer() -> TitleNormalizer:
    global _normalizer
    if _normalizer is None:
        _normalizer = TitleNormalizer()
    return _normalizer


def normalize_title(filename: str, folder_name: str = "") -> Tuple[str, Optional[int], str]:
    """
    Convenience function to normalize a title
    Returns: (title, year, method)
    """
    return get_normalizer().normalize(filename, folder_name)
