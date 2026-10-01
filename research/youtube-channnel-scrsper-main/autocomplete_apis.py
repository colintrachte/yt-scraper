"""
YouTube Autocomplete API for Keyword Expansion
Focused implementation for YouTube-specific keyword discovery with quota management
"""

import requests
import re
import time
import json
import pickle
import os
from typing import List, Dict, Optional
from urllib.parse import quote
import logging
from datetime import datetime, timedelta

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class YouTubeAutocompleteManager:
    """Manages YouTube autocomplete API for keyword expansion with quota management"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
        
        # Quota management
        self.cache_file = "youtube_autocomplete_cache.pkl"
        self.cache_duration = config.get('cache_duration_hours', 24)  # Cache for 24 hours by default
        self.max_requests_per_minute = config.get('max_requests_per_minute', 10)
        self.request_timestamps = []
        self.cache = self._load_cache()
        
        # Alternative sources for quota exhaustion
        self.use_alternative_sources = config.get('use_alternative_sources', True)
        self.fallback_to_static = config.get('fallback_to_static', True)
    
    def _load_cache(self) -> Dict:
        """Load cache from file"""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'rb') as f:
                    cache = pickle.load(f)
                    # Clean expired entries
                    current_time = datetime.now()
                    cleaned_cache = {}
                    for key, (data, timestamp) in cache.items():
                        if current_time - timestamp < timedelta(hours=self.cache_duration):
                            cleaned_cache[key] = (data, timestamp)
                    return cleaned_cache
        except Exception as e:
            logger.warning(f"Failed to load cache: {e}")
        return {}
    
    def _save_cache(self):
        """Save cache to file"""
        try:
            with open(self.cache_file, 'wb') as f:
                pickle.dump(self.cache, f)
        except Exception as e:
            logger.warning(f"Failed to save cache: {e}")
    
    def _get_cache_key(self, keyword: str, region: str) -> str:
        """Generate cache key for keyword and region"""
        return f"{keyword.lower()}_{region.lower()}"
    
    def _is_quota_exhausted(self) -> bool:
        """Check if we're hitting rate limits"""
        current_time = time.time()
        # Remove timestamps older than 1 minute
        self.request_timestamps = [ts for ts in self.request_timestamps if current_time - ts < 60]
        
        # Check if we've exceeded the rate limit
        if len(self.request_timestamps) >= self.max_requests_per_minute:
            return True
        return False
    
    def _wait_for_quota_reset(self):
        """Wait for quota to reset"""
        if self.request_timestamps:
            oldest_request = min(self.request_timestamps)
            wait_time = 60 - (time.time() - oldest_request)
            if wait_time > 0:
                logger.info(f"Rate limit reached. Waiting {wait_time:.1f} seconds...")
                time.sleep(wait_time)
    
    def _make_request_with_quota_management(self, url: str, params: Dict) -> Optional[requests.Response]:
        """Make request with quota management"""
        # Check cache first
        cache_key = self._get_cache_key(params.get('q', ''), params.get('hl', 'us'))
        if cache_key in self.cache:
            logger.info(f"Using cached results for: {params.get('q', '')}")
            return None  # Will use cached data
        
        # Check rate limits
        if self._is_quota_exhausted():
            if self.fallback_to_static:
                logger.warning("Quota exhausted, using static fallback")
                return None
            else:
                self._wait_for_quota_reset()
        
        try:
            response = self.session.get(url, params=params, timeout=10)
            self.request_timestamps.append(time.time())
            
            # Cache successful responses
            if response.status_code == 200:
                self.cache[cache_key] = (response, datetime.now())
                self._save_cache()
            
            return response
        except Exception as e:
            logger.warning(f"Request failed: {e}")
            return None
    
    def _get_static_fallback_suggestions(self, keyword: str) -> List[str]:
        """Get static fallback suggestions when APIs are exhausted"""
        # Pre-defined YouTube-related suggestions based on common patterns
        static_suggestions = {
            'music': ['music video', 'music download', 'music player', 'music box', 'music live', 'music cover', 'music remix'],
            'cooking': ['cooking class', 'cooking game', 'cooking tutorial', 'cooking tips', 'cooking show', 'cooking challenge'],
            'gaming': ['gaming pc', 'gaming chair', 'gaming setup', 'gaming laptop', 'gaming mobile', 'gaming house'],
            'fitness': ['fitness workout', 'fitness tips', 'fitness routine', 'fitness motivation', 'fitness challenge'],
            'tech': ['tech review', 'tech news', 'tech tips', 'tech tutorial', 'tech unboxing', 'tech comparison'],
            'travel': ['travel vlog', 'travel tips', 'travel guide', 'travel food', 'travel adventure'],
            'education': ['education tips', 'education system', 'education technology', 'education reform'],
            'business': ['business tips', 'business ideas', 'business strategy', 'business motivation'],
            'art': ['art tutorial', 'art tips', 'art challenge', 'art supplies', 'art techniques'],
            'sports': ['sports highlights', 'sports news', 'sports tips', 'sports training', 'sports analysis']
        }
        
        # Find matching suggestions
        keyword_lower = keyword.lower()
        suggestions = []
        
        for category, category_suggestions in static_suggestions.items():
            if category in keyword_lower or keyword_lower in category:
                suggestions.extend(category_suggestions)
        
        # Add generic YouTube modifiers
        generic_modifiers = ['tutorial', 'tips', 'guide', 'review', 'reaction', 'cover', 'remix', 'live', 'official', 'best', 'top']
        for modifier in generic_modifiers:
            suggestions.append(f"{keyword} {modifier}")
            suggestions.append(f"{modifier} {keyword}")
        
        return list(set(suggestions))[:10]  # Remove duplicates and limit
    
    def get_youtube_autocomplete(self, keyword: str, region: str = "US", max_results: int = 10) -> List[str]:
        """Get YouTube autocomplete suggestions using multiple methods with quota management"""
        suggestions = set()
        cache_key = self._get_cache_key(keyword, region)
        
        # Check cache first
        if cache_key in self.cache:
            cached_response, _ = self.cache[cache_key]
            try:
                data = cached_response.json()
                if len(data) > 1:
                    suggestions.update(data[1][:max_results])
                    logger.info(f"Using cached YouTube autocomplete for: {keyword}")
                    return list(suggestions)[:max_results]
            except:
                pass
        
        # Method 1: YouTube-specific autocomplete with quota management
        try:
            url = "https://suggestqueries.google.com/complete/search"
            params = {
                'client': 'youtube',
                'ds': 'yt',
                'hl': region.lower(),
                'q': keyword
            }
            
            response = self._make_request_with_quota_management(url, params)
            if response and response.status_code == 200:
                # Try to parse as JSON first
                try:
                    data = response.json()
                    if len(data) > 1:
                        suggestions.update(data[1][:max_results])
                except json.JSONDecodeError:
                    # Fallback to regex parsing for text response
                    text = response.text
                    youtube_suggestions = re.findall(r'"(.*?)"', text)
                    suggestions.update([s for s in youtube_suggestions if s.lower() != keyword.lower()][:max_results])
        except Exception as e:
            logger.warning(f"YouTube autocomplete method 1 failed: {e}")
        
        # Method 2: Alternative YouTube autocomplete endpoint (only if we have quota)
        if not self._is_quota_exhausted() and len(suggestions) < max_results:
            try:
                url = "https://clients1.google.com/complete/search"
                params = {
                    'client': 'youtube',
                    'hl': region.lower(),
                    'gl': region.lower(),
                    'q': keyword,
                    'callback': 'window.google.ac.h'
                }
                
                response = self._make_request_with_quota_management(url, params)
                if response and response.status_code == 200:
                    text = response.text
                    # Extract suggestions from callback response
                    suggestions_text = re.findall(r'\["(.*?)"', text)
                    for suggestion in suggestions_text:
                        if suggestion and suggestion.lower() != keyword.lower():
                            suggestions.add(suggestion)
            except Exception as e:
                logger.warning(f"YouTube autocomplete method 2 failed: {e}")
        
        # Method 3: YouTube search suggestions via different client (only if we have quota)
        if not self._is_quota_exhausted() and len(suggestions) < max_results:
            try:
                url = "https://suggestqueries.google.com/complete/search"
                params = {
                    'client': 'firefox',
                    'ds': 'yt',
                    'hl': region.lower(),
                    'q': keyword
                }
                
                response = self._make_request_with_quota_management(url, params)
                if response and response.status_code == 200:
                    try:
                        data = response.json()
                        if len(data) > 1:
                            suggestions.update(data[1][:max_results])
                    except json.JSONDecodeError:
                        text = response.text
                        firefox_suggestions = re.findall(r'"(.*?)"', text)
                        suggestions.update([s for s in firefox_suggestions if s.lower() != keyword.lower()][:max_results])
            except Exception as e:
                logger.warning(f"YouTube autocomplete method 3 failed: {e}")
        
        # If we still don't have enough suggestions and quota is exhausted, use static fallback
        if len(suggestions) < max_results and self.fallback_to_static:
            logger.info(f"Using static fallback for: {keyword}")
            static_suggestions = self._get_static_fallback_suggestions(keyword)
            suggestions.update(static_suggestions)
        
        # Convert to list and filter
        result = []
        for suggestion in suggestions:
            if suggestion and suggestion.strip() and suggestion.lower() != keyword.lower():
                result.append(suggestion.strip())
        
        return result[:max_results]
    
    def get_youtube_long_tail_variations(self, keyword: str, region: str = "US") -> List[str]:
        """Generate YouTube-specific long-tail keyword variations"""
        variations = set()
        
        # Add base keyword
        variations.add(keyword)
        
        # YouTube-specific question starters
        youtube_questions = ["how to", "what is", "why", "when", "where", "which", "who", "how does"]
        for starter in youtube_questions:
            query = f"{starter} {keyword}"
            suggestions = self.get_youtube_autocomplete(query, region, 3)
            variations.update(suggestions)
        
        # YouTube-specific modifiers
        youtube_modifiers = ["tutorial", "guide", "tips", "tricks", "review", "reaction", "cover", 
                           "remix", "live", "official", "best", "top", "new", "latest", "viral"]
        for modifier in youtube_modifiers:
            query = f"{modifier} {keyword}"
            suggestions = self.get_youtube_autocomplete(query, region, 3)
            variations.update(suggestions)
            query = f"{keyword} {modifier}"
            suggestions = self.get_youtube_autocomplete(query, region, 3)
            variations.update(suggestions)
        
        # Year-based variations
        current_year = "2024"
        year_queries = [f"{keyword} {current_year}", f"{current_year} {keyword}"]
        for query in year_queries:
            suggestions = self.get_youtube_autocomplete(query, region, 3)
            variations.update(suggestions)
        
        # YouTube content type variations
        content_types = ["video", "song", "music", "movie", "show", "episode", "clip"]
        for content_type in content_types:
            query = f"{keyword} {content_type}"
            suggestions = self.get_youtube_autocomplete(query, region, 2)
            variations.update(suggestions)
        
        return list(variations)[:self.config.get('max_long_tail_variations', 15)]
    
    def get_comprehensive_youtube_suggestions(self, keyword: str, region: str = "US", max_results: int = 20) -> List[str]:
        """Get comprehensive YouTube keyword suggestions"""
        all_suggestions = set()
        
        # Get direct YouTube autocomplete suggestions
        direct_suggestions = self.get_youtube_autocomplete(keyword, region, max_results)
        all_suggestions.update(direct_suggestions)
        
        # Get long-tail variations if enabled
        if self.config.get('include_long_tail', True):
            long_tail_variations = self.get_youtube_long_tail_variations(keyword, region)
            all_suggestions.update(long_tail_variations)
        
        # Convert to list and filter
        result = []
        for suggestion in all_suggestions:
            if suggestion and suggestion.strip() and suggestion.lower() != keyword.lower():
                result.append(suggestion.strip())
        
        return result[:max_results]

def create_autocomplete_manager(config: Dict) -> YouTubeAutocompleteManager:
    """Factory function to create YouTubeAutocompleteManager instance"""
    return YouTubeAutocompleteManager(config)
