"""
Web Search Module for Title Verification
Uses DuckDuckGo HTML search (no API key required)
"""

import re
import urllib.parse
import logging
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass
import time

logger = logging.getLogger(__name__)

# Try to import requests
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    logger.warning("requests library not available, web search disabled")


@dataclass
class SearchResult:
    """Represents a web search result."""
    title: str
    url: str
    snippet: str


class WebSearcher:
    """
    Web search for verifying and correcting media titles.
    Uses DuckDuckGo HTML search (no API key needed).
    """
    
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
    }
    
    # Common patterns to extract year from search results
    YEAR_PATTERN = re.compile(r'\((\d{4})\)|\b(19\d{2}|20[0-2]\d)\b')
    
    # Patterns to identify media type from search results
    TV_INDICATORS = ['tv series', 'tv show', 'series', 'season', 'episode', 'episodes', 'miniseries']
    MOVIE_INDICATORS = ['film', 'movie', 'feature film']
    
    def __init__(self):
        """Initialize web searcher."""
        self.session = None
        if REQUESTS_AVAILABLE:
            self.session = requests.Session()
            self.session.headers.update(self.HEADERS)
        self.last_search_time = 0
        self.min_delay = 1.0  # Minimum delay between searches (be nice to servers)
    
    def _rate_limit(self):
        """Enforce rate limiting."""
        elapsed = time.time() - self.last_search_time
        if elapsed < self.min_delay:
            time.sleep(self.min_delay - elapsed)
        self.last_search_time = time.time()
    
    def search_title(self, title: str, year: Optional[int] = None, 
                     media_type: Optional[str] = None) -> Optional[Dict]:
        """
        Search for a media title and return verified information.
        
        Args:
            title: The title to search for
            year: Optional year hint
            media_type: Optional hint ('movie' or 'tv')
        
        Returns:
            Dict with verified title info or None
        """
        if not self.session:
            return None
        
        # Build search query
        query_parts = [title]
        if year:
            query_parts.append(str(year))
        
        # Add TMDB/IMDB to get authoritative results
        if media_type == 'movie':
            query_parts.append('movie TMDB')
        elif media_type in ('tv', 'kdrama', 'anime'):
            query_parts.append('TV series TMDB')
        else:
            query_parts.append('TMDB')
        
        query = ' '.join(query_parts)
        
        try:
            self._rate_limit()
            results = self._search_duckduckgo(query)
            
            if results:
                return self._analyze_results(results, title, year, media_type)
            
        except Exception as e:
            logger.warning(f"Web search failed for '{title}': {e}")
        
        return None
    
    def _search_duckduckgo(self, query: str) -> List[SearchResult]:
        """
        Search DuckDuckGo and return results.
        """
        results = []
        
        try:
            # Use DuckDuckGo HTML search
            url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
            
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            
            html = response.text
            
            # Parse results using regex (avoid BeautifulSoup dependency)
            # Look for result divs
            result_pattern = re.compile(
                r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>([^<]+)</a>.*?'
                r'<a[^>]+class="result__snippet"[^>]*>([^<]*(?:<[^>]+>[^<]*)*)</a>',
                re.DOTALL | re.IGNORECASE
            )
            
            # Simpler pattern for snippets
            snippet_pattern = re.compile(
                r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>([^<]+)</a>',
                re.IGNORECASE
            )
            
            matches = snippet_pattern.findall(html)
            
            for url, title in matches[:5]:  # Top 5 results
                # Clean up the URL (DuckDuckGo redirects)
                if 'uddg=' in url:
                    url_match = re.search(r'uddg=([^&]+)', url)
                    if url_match:
                        url = urllib.parse.unquote(url_match.group(1))
                
                # Clean HTML from title
                title = re.sub(r'<[^>]+>', '', title).strip()
                
                if title:
                    results.append(SearchResult(
                        title=title,
                        url=url,
                        snippet=""
                    ))
            
            logger.debug(f"Web search for '{query}' returned {len(results)} results")
            
        except requests.Timeout:
            logger.warning(f"Web search timeout for '{query}'")
        except Exception as e:
            logger.warning(f"Web search error: {e}")
        
        return results
    
    def _analyze_results(self, results: List[SearchResult], 
                         original_title: str, year: Optional[int],
                         media_type: Optional[str]) -> Optional[Dict]:
        """
        Analyze search results to extract verified information.
        """
        if not results:
            return None
        
        verified = {
            'original_query': original_title,
            'verified_title': None,
            'year': year,
            'media_type': media_type,
            'confidence': 0.0,
            'source': None
        }
        
        for result in results:
            title_lower = result.title.lower()
            url_lower = result.url.lower()
            
            # Check if it's from authoritative source
            is_tmdb = 'themoviedb.org' in url_lower or 'tmdb' in url_lower
            is_imdb = 'imdb.com' in url_lower
            
            if is_tmdb or is_imdb:
                # Extract title from result
                clean_title = self._extract_title_from_result(result.title)
                
                if clean_title:
                    # Extract year if present
                    year_match = self.YEAR_PATTERN.search(result.title)
                    if year_match:
                        found_year = int(year_match.group(1) or year_match.group(2))
                        verified['year'] = found_year
                    
                    # Determine media type from URL/title
                    if '/tv/' in url_lower or any(ind in title_lower for ind in self.TV_INDICATORS):
                        verified['media_type'] = 'tv'
                    elif '/movie/' in url_lower or any(ind in title_lower for ind in self.MOVIE_INDICATORS):
                        verified['media_type'] = 'movie'
                    
                    verified['verified_title'] = clean_title
                    verified['source'] = 'tmdb' if is_tmdb else 'imdb'
                    verified['confidence'] = 0.9 if is_tmdb else 0.85
                    
                    logger.info(f"Web search verified: '{original_title}' -> '{clean_title}' ({verified['year']})")
                    return verified
        
        # If no authoritative source found, try to extract from first result
        if results:
            first = results[0]
            clean_title = self._extract_title_from_result(first.title)
            
            if clean_title and self._titles_similar(original_title, clean_title):
                year_match = self.YEAR_PATTERN.search(first.title)
                if year_match:
                    verified['year'] = int(year_match.group(1) or year_match.group(2))
                
                verified['verified_title'] = clean_title
                verified['source'] = 'web'
                verified['confidence'] = 0.5
                return verified
        
        return None
    
    def _extract_title_from_result(self, result_title: str) -> Optional[str]:
        """Extract clean title from search result."""
        # Remove common suffixes
        title = result_title
        
        # Remove year in parentheses for now (we extract it separately)
        title = re.sub(r'\s*\(\d{4}\)\s*', ' ', title)
        
        # Remove common suffixes
        suffixes_to_remove = [
            r'\s*[-—|]\s*TMDB.*$',
            r'\s*[-—|]\s*The Movie Database.*$',
            r'\s*[-—|]\s*IMDb.*$',
            r'\s*[-—|]\s*Wikipedia.*$',
            r'\s*[-—|]\s*TV Series.*$',
            r'\s*[-—|]\s*Movie.*$',
            r'\s*[-—|]\s*Film.*$',
        ]
        
        for suffix in suffixes_to_remove:
            title = re.sub(suffix, '', title, flags=re.IGNORECASE)
        
        title = title.strip()
        
        return title if title else None
    
    def _titles_similar(self, title1: str, title2: str) -> bool:
        """Check if two titles are similar enough."""
        # Normalize both titles
        def normalize(t):
            t = t.lower()
            t = re.sub(r'[^\w\s]', '', t)
            t = re.sub(r'\s+', ' ', t).strip()
            return t
        
        n1 = normalize(title1)
        n2 = normalize(title2)
        
        # Check if one contains the other
        if n1 in n2 or n2 in n1:
            return True
        
        # Check word overlap
        words1 = set(n1.split())
        words2 = set(n2.split())
        
        if not words1 or not words2:
            return False
        
        overlap = len(words1 & words2)
        min_words = min(len(words1), len(words2))
        
        return overlap / min_words >= 0.5


def verify_title_with_web_search(title: str, year: Optional[int] = None,
                                  media_type: Optional[str] = None) -> Optional[Dict]:
    """
    Convenience function to verify a title using web search.
    
    Args:
        title: The title to verify
        year: Optional year hint  
        media_type: Optional media type hint
    
    Returns:
        Dict with verified info or None
    """
    searcher = WebSearcher()
    return searcher.search_title(title, year, media_type)
