"""
TMDB API Integration for Media File Organizer
Matches parsed filenames to TMDB entries for accurate metadata
With AI-powered fallback for unrecognized filenames
"""

import logging
import time
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass

import requests

from filename_parser import ParsedFilename
from title_normalizer import normalize_title, get_normalizer

logger = logging.getLogger(__name__)


@dataclass
class TMDBMatch:
    """Represents a TMDB match result."""
    tmdb_id: int
    tmdb_type: str  # 'movie' or 'tv'
    title: str
    original_title: str
    year: Optional[int]
    overview: str
    poster_path: Optional[str]
    vote_average: float
    confidence: float  # Match confidence 0-1


class TMDBError(Exception):
    """Custom exception for TMDB API errors."""
    pass


class TMDBMatcher:
    """
    Matches media filenames to TMDB entries.
    Uses TMDB API v3.
    """
    
    BASE_URL = "https://api.themoviedb.org/3"
    
    def __init__(self, api_key: str, language: str = "en-US", include_adult: bool = False):
        """
        Initialize TMDB matcher.
        
        Args:
            api_key: TMDB API key
            language: Language for results (default: en-US)
            include_adult: Include adult content in searches
        """
        self.api_key = api_key
        self.language = language
        self.include_adult = include_adult
        self._session = requests.Session()
        self._last_request_time = 0
        self._rate_limit_delay = 0.25  # 4 requests per second max
    
    def _rate_limit(self):
        """Apply rate limiting to avoid API throttling."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self._rate_limit_delay:
            time.sleep(self._rate_limit_delay - elapsed)
        self._last_request_time = time.time()
    
    def _request(self, endpoint: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Make a request to TMDB API.
        
        Args:
            endpoint: API endpoint (e.g., '/search/movie')
            params: Query parameters
        
        Returns:
            JSON response as dict
        """
        self._rate_limit()
        
        url = f"{self.BASE_URL}{endpoint}"
        params = params or {}
        params['api_key'] = self.api_key
        params['language'] = self.language
        
        try:
            response = self._session.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            logger.error(f"TMDB request timed out: {endpoint}")
            raise TMDBError("Request timed out")
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                raise TMDBError("Invalid TMDB API key")
            elif e.response.status_code == 429:
                # Rate limited - wait and retry
                retry_after = int(e.response.headers.get('Retry-After', 10))
                logger.warning(f"Rate limited, waiting {retry_after}s")
                time.sleep(retry_after)
                return self._request(endpoint, params)
            else:
                raise TMDBError(f"HTTP error: {e}")
        except requests.exceptions.RequestException as e:
            logger.error(f"TMDB request failed: {e}")
            raise TMDBError(f"Request failed: {e}")
    
    def search_movie(self, title: str, year: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Search for movies by title.
        
        Args:
            title: Movie title to search
            year: Optional release year for better matching
        
        Returns:
            List of movie results
        """
        params = {
            'query': title,
            'include_adult': str(self.include_adult).lower()
        }
        if year:
            params['year'] = year
        
        data = self._request('/search/movie', params)
        return data.get('results', [])
    
    def search_tv(self, title: str, year: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Search for TV shows by title.
        
        Args:
            title: TV show title to search
            year: Optional first air year for better matching
        
        Returns:
            List of TV show results
        """
        params = {
            'query': title,
            'include_adult': str(self.include_adult).lower()
        }
        if year:
            params['first_air_date_year'] = year
        
        data = self._request('/search/tv', params)
        return data.get('results', [])
    
    def search_multi(self, title: str) -> List[Dict[str, Any]]:
        """
        Search for both movies and TV shows.
        
        Args:
            title: Title to search
        
        Returns:
            List of mixed results with 'media_type' field
        """
        params = {
            'query': title,
            'include_adult': str(self.include_adult).lower()
        }
        
        data = self._request('/search/multi', params)
        return data.get('results', [])
    
    def get_movie_details(self, movie_id: int) -> Dict[str, Any]:
        """Get detailed movie information."""
        return self._request(f'/movie/{movie_id}')
    
    def get_tv_details(self, tv_id: int) -> Dict[str, Any]:
        """Get detailed TV show information."""
        return self._request(f'/tv/{tv_id}')
    
    def _calculate_match_confidence(self, result: Dict[str, Any], 
                                     parsed: ParsedFilename,
                                     is_tv: bool) -> float:
        """
        Calculate confidence score for a match.
        
        Args:
            result: TMDB search result
            parsed: Parsed filename info
            is_tv: Whether searching for TV show
        
        Returns:
            Confidence score 0.0 to 1.0
        """
        confidence = 0.0
        
        # Title similarity
        result_title = result.get('title', result.get('name', '')).lower()
        parsed_title = parsed.title.lower()
        
        if parsed_title == result_title:
            confidence += 0.5
        elif parsed_title in result_title or result_title in parsed_title:
            confidence += 0.3
        else:
            # Check word overlap
            parsed_words = set(parsed_title.split())
            result_words = set(result_title.split())
            overlap = len(parsed_words & result_words) / max(len(parsed_words), 1)
            confidence += overlap * 0.3
        
        # Year match
        if parsed.year:
            release_date = result.get('release_date', result.get('first_air_date', ''))
            if release_date:
                result_year = int(release_date[:4]) if len(release_date) >= 4 else None
                if result_year == parsed.year:
                    confidence += 0.3
                elif result_year and abs(result_year - parsed.year) <= 1:
                    confidence += 0.15
        
        # Popularity boost (more popular = more likely correct)
        popularity = result.get('popularity', 0)
        if popularity > 100:
            confidence += 0.1
        elif popularity > 50:
            confidence += 0.05
        
        # Vote count (more votes = more established)
        vote_count = result.get('vote_count', 0)
        if vote_count > 1000:
            confidence += 0.1
        elif vote_count > 100:
            confidence += 0.05
        
        return min(confidence, 1.0)
    
    def _search_with_title(self, title: str, year: Optional[int], 
                            is_series: bool, parsed: ParsedFilename) -> Tuple[Optional[TMDBMatch], float]:
        """
        Internal search helper that returns best match and confidence.
        """
        best_match = None
        best_confidence = 0.0
        
        # Create a temporary parsed object with the new title
        temp_parsed = ParsedFilename(
            original_filename=parsed.original_filename,
            title=title,
            year=year or parsed.year,
            season=parsed.season,
            episode=parsed.episode,
            quality=parsed.quality,
            is_series=parsed.is_series,
            extension=parsed.extension,
            languages=parsed.languages
        )
        
        # Try TV search first if it looks like a series
        if is_series:
            results = self.search_tv(title, year)
            for result in results[:5]:
                confidence = self._calculate_match_confidence(result, temp_parsed, is_tv=True)
                if confidence > best_confidence:
                    best_confidence = confidence
                    release_date = result.get('first_air_date', '')
                    best_match = TMDBMatch(
                        tmdb_id=result['id'],
                        tmdb_type='tv',
                        title=result.get('name', ''),
                        original_title=result.get('original_name', ''),
                        year=int(release_date[:4]) if len(release_date) >= 4 else None,
                        overview=result.get('overview', ''),
                        poster_path=result.get('poster_path'),
                        vote_average=result.get('vote_average', 0),
                        confidence=confidence
                    )
        
        # Try movie search if not series or if TV search had low confidence
        if not is_series or best_confidence < 0.5:
            results = self.search_movie(title, year)
            for result in results[:5]:
                confidence = self._calculate_match_confidence(result, temp_parsed, is_tv=False)
                if confidence > best_confidence:
                    best_confidence = confidence
                    release_date = result.get('release_date', '')
                    best_match = TMDBMatch(
                        tmdb_id=result['id'],
                        tmdb_type='movie',
                        title=result.get('title', ''),
                        original_title=result.get('original_title', ''),
                        year=int(release_date[:4]) if len(release_date) >= 4 else None,
                        overview=result.get('overview', ''),
                        poster_path=result.get('poster_path'),
                        vote_average=result.get('vote_average', 0),
                        confidence=confidence
                    )
        
        return best_match, best_confidence

    def match(self, parsed: ParsedFilename, content_type: str = None, 
              folder_name: str = "") -> Optional[TMDBMatch]:
        """
        Find the best TMDB match for a parsed filename.
        Uses AI-powered fallback when initial search fails.
        
        Args:
            parsed: ParsedFilename from parser
            content_type: Hint for content type ('movie', 'tvshow', 'anime', 'kdrama')
            folder_name: Original folder name for context
        
        Returns:
            TMDBMatch if found, None otherwise
        """
        # Determine search type
        is_series = parsed.is_series or content_type in ('tvshow', 'anime', 'kdrama')
        
        logger.info(f"Matching: '{parsed.title}' (year={parsed.year}, series={is_series})")
        
        # Step 1: Try with original parsed title
        best_match, best_confidence = self._search_with_title(
            parsed.title, parsed.year, is_series, parsed
        )
        
        # Step 2: If no good match (confidence < 0.4), try with AI/heuristic normalization
        if best_confidence < 0.4:
            logger.info(f"Low confidence ({best_confidence:.2f}), trying AI normalization...")
            
            try:
                normalized_title, normalized_year, method = normalize_title(
                    parsed.original_filename, folder_name
                )
                
                # Only retry if normalized title is different
                if normalized_title.lower() != parsed.title.lower():
                    logger.info(f"Normalized title: '{parsed.title}' -> '{normalized_title}' (via {method})")
                    
                    fallback_match, fallback_confidence = self._search_with_title(
                        normalized_title, normalized_year or parsed.year, is_series, parsed
                    )
                    
                    if fallback_confidence > best_confidence:
                        best_match = fallback_match
                        best_confidence = fallback_confidence
                        logger.info(f"Fallback improved match: confidence {fallback_confidence:.2f}")
            except Exception as e:
                logger.warning(f"AI normalization failed: {e}")
        
        # Step 3: If still no match, try with just the first few words
        if best_confidence < 0.3 and best_match is None:
            # Try first 2-3 significant words only
            words = parsed.title.split()[:3]
            if len(words) >= 1:
                short_title = ' '.join(words)
                logger.info(f"Trying short title: '{short_title}'")
                
                short_match, short_confidence = self._search_with_title(
                    short_title, parsed.year, is_series, parsed
                )
                
                if short_confidence > best_confidence:
                    best_match = short_match
                    best_confidence = short_confidence
        
        if best_match:
            logger.info(f"Matched: '{parsed.title}' -> '{best_match.title}' "
                       f"(ID: {best_match.tmdb_id}, type: {best_match.tmdb_type}, "
                       f"confidence: {best_match.confidence:.2f})")
        else:
            logger.warning(f"No match found for: '{parsed.title}'")
        
        return best_match
    
    def verify_api_key(self) -> bool:
        """Verify the API key is valid."""
        try:
            self._request('/configuration')
            logger.info("TMDB API key verified")
            return True
        except TMDBError:
            logger.error("TMDB API key verification failed")
            return False
