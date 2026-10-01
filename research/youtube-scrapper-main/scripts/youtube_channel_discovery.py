#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube Channel Discovery via Google Search
Finds YouTube channels by category and location using Google Search API or browser scraping
No account/API key required - uses browser-based Google search
"""

import asyncio
import json
import os
import sys
import logging
import time
import re
from typing import List, Dict, Optional, Set
from pathlib import Path
from playwright.async_api import async_playwright, Page
from datetime import datetime, timezone
import random
from urllib.parse import quote_plus, urlparse, parse_qs
from dotenv import load_dotenv

# Import shared anti-detection module
try:
    from anti_detection import AntiDetectionManager, get_stealth_scripts
except ImportError:
    # Fallback if module not found
    AntiDetectionManager = None
    get_stealth_scripts = None

# Import proxy manager
try:
    from proxy_manager import ProxyManager
except ImportError:
    ProxyManager = None

# Set UTF-8 encoding for stdout
if sys.platform == 'win32':
    try:
        import codecs
        if hasattr(sys.stdout, 'buffer'):
            sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
        if hasattr(sys.stderr, 'buffer'):
            sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')
    except Exception:
        pass  # Already wrapped or not available

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class YouTubeChannelDiscovery:
    """Discover YouTube channels via Google Search"""

    def __init__(self, config_path: str = None):
        if config_path is None:
            config_path = Path(__file__).parent / 'config' / 'scraper_config.json'
        self.config_path = config_path
        self.config = self._load_config(str(config_path))
        self.browser = None
        self.context = None
        self.page = None
        self.discovered_channels: Set[str] = set()
        
        # Detect region from config path
        self.region = self._detect_region(str(config_path))

        # Initialize proxy manager
        self.proxy_manager = self._init_proxy_manager()

        # Queue directory (region-specific)
        base_queue_dir = Path(__file__).parent / 'data' / 'queue'
        if self.region:
            self.queue_dir = base_queue_dir / self.region
        else:
            self.queue_dir = base_queue_dir
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        
        # Progress directory
        self.progress_dir = Path(__file__).parent / 'data' / 'progress'
        self.progress_dir.mkdir(parents=True, exist_ok=True)

    def _detect_region(self, config_path: str) -> Optional[str]:
        """Detect region from config file path"""
        config_path_lower = config_path.lower()
        if '_us' in config_path_lower:
            return 'us'
        elif '_uk' in config_path_lower:
            return 'uk'
        elif '_eur' in config_path_lower:
            return 'eur'
        elif '_east' in config_path_lower:
            return 'east'
        elif '_gulf' in config_path_lower:
            return 'gulf'
        elif '_ind' in config_path_lower:
            return 'ind'
        return None

    def _load_config(self, config_path: str) -> Dict:
        """Load configuration from JSON file"""
        try:
            with open(config_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load config: {e}. Using defaults.")
            return {
                'categories': [
                    'gaming', 'tech', 'beauty', 'fashion', 'fitness',
                    'food', 'travel', 'music', 'education', 'comedy',
                    'vlog', 'lifestyle', 'cooking', 'diy', 'art'
                ],
                'locations': [
                    'India', 'Mumbai', 'Delhi', 'Bangalore', 'Hyderabad',
                    'Chennai', 'Kolkata', 'Pune', 'Ahmedabad'
                ],
                'results_per_search': 20,
                'search_delay': (3, 7)
            }

    def _init_proxy_manager(self):
        """Initialize proxy manager from config or environment variables"""
        if ProxyManager is None:
            return None

        # Try environment variables first
        pm = ProxyManager.from_env()
        if pm.enabled:
            logger.info(f"Proxy loaded from environment: {pm}")
            return pm

        # Fall back to config file
        pm = ProxyManager.from_config(str(self.config_path))
        if pm.enabled:
            logger.info(f"Proxy loaded from config: {pm}")
            return pm

        logger.debug("No proxy configured — running without proxy")
        return pm

    async def start_browser(self, headless: bool = True):
        """Start Playwright browser with advanced anti-detection"""
        logger.info("Starting browser for discovery with anti-detection...")
        self.playwright = await async_playwright().start()

        # Get anti-detection configuration
        if AntiDetectionManager:
            self.anti_detection = AntiDetectionManager()
            fingerprint = self.anti_detection.generate_fingerprint()
            chrome_args = self.anti_detection.get_chrome_args()
        else:
            fingerprint = self._get_default_fingerprint()
            chrome_args = self._get_default_chrome_args()

        self.browser = await self.playwright.chromium.launch(
            headless=headless,
            args=chrome_args
        )

        # Build proxy config if proxy manager is enabled
        proxy_config = None
        if self.proxy_manager and self.proxy_manager.enabled:
            proxy_config = self.proxy_manager.get_playwright_proxy()
            if proxy_config:
                logger.info(f"Proxy enabled: {self.proxy_manager}")
                logger.info(f"Browser using proxy: {self.proxy_manager.provider} → {self.proxy_manager.host}:{self.proxy_manager.port}")

        context_kwargs = dict(
            viewport=fingerprint['viewport'],
            user_agent=fingerprint['user_agent'],
            locale=fingerprint['locale'],
            timezone_id=fingerprint['timezone'],
            color_scheme=fingerprint.get('color_scheme', 'light'),
            device_scale_factor=fingerprint.get('device_scale_factor', 1),
            has_touch=fingerprint.get('has_touch', False),
        )
        if proxy_config:
            context_kwargs['proxy'] = proxy_config

        self.context = await self.browser.new_context(**context_kwargs)

        self.page = await self.context.new_page()

        # Inject comprehensive stealth scripts
        if get_stealth_scripts:
            stealth_js = get_stealth_scripts(fingerprint)
        else:
            stealth_js = self._get_default_stealth_script()

        await self.page.add_init_script(stealth_js)

        # Block known fingerprinting scripts
        await self._setup_request_interception()

        logger.info(f"Browser started with fingerprint: {fingerprint['user_agent'][:50]}...")

    def _get_default_fingerprint(self) -> Dict:
        """Generate default fingerprint if anti_detection module unavailable"""
        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
        ]
        viewports = [
            {'width': 1920, 'height': 1080},
            {'width': 1366, 'height': 768},
            {'width': 1536, 'height': 864},
            {'width': 1440, 'height': 900},
            {'width': 2560, 'height': 1440},
        ]
        timezones = ['America/New_York', 'America/Los_Angeles', 'Europe/London', 'Asia/Kolkata', 'Asia/Tokyo']
        locales = ['en-US', 'en-GB', 'en-IN']

        return {
            'user_agent': random.choice(user_agents),
            'viewport': random.choice(viewports),
            'timezone': random.choice(timezones),
            'locale': random.choice(locales),
            'color_scheme': random.choice(['light', 'dark']),
            'device_scale_factor': random.choice([1, 1.25, 1.5, 2]),
            'has_touch': False,
        }

    def _get_default_chrome_args(self) -> List[str]:
        """Get default Chrome launch arguments for anti-detection"""
        return [
            '--disable-blink-features=AutomationControlled',
            '--disable-dev-shm-usage',
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-infobars',
            '--disable-extensions',
            '--disable-plugins-discovery',
            '--disable-default-apps',
            '--disable-component-extensions-with-background-pages',
            '--disable-background-timer-throttling',
            '--disable-backgrounding-occluded-windows',
            '--disable-renderer-backgrounding',
            '--window-size=1920,1080',
            '--start-maximized',
            '--no-first-run',
            '--no-default-browser-check',
            '--disable-gpu',
            f'--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        ]

    def _get_default_stealth_script(self) -> str:
        """Get comprehensive stealth JavaScript"""
        return '''
            // Hide webdriver property
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
                configurable: true
            });
            delete navigator.__proto__.webdriver;

            // Mock chrome runtime
            window.chrome = {
                runtime: {
                    id: undefined,
                    connect: () => {},
                    sendMessage: () => {},
                    onMessage: { addListener: () => {} },
                },
                loadTimes: () => ({}),
                csi: () => ({}),
            };

            // Override permissions API
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => {
                if (parameters.name === 'notifications') {
                    return Promise.resolve({ state: Notification.permission });
                }
                return originalQuery.call(navigator.permissions, parameters);
            };

            // Override plugins to look realistic
            Object.defineProperty(navigator, 'plugins', {
                get: () => {
                    const plugins = [
                        { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                        { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                        { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
                    ];
                    plugins.length = 3;
                    return plugins;
                },
            });

            // Override languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en'],
            });

            // Hide automation indicators in navigator
            Object.defineProperty(navigator, 'maxTouchPoints', {
                get: () => 0,
            });

            // Override hardware concurrency to common value
            Object.defineProperty(navigator, 'hardwareConcurrency', {
                get: () => 8,
            });

            // Override device memory
            Object.defineProperty(navigator, 'deviceMemory', {
                get: () => 8,
            });

            // Mock WebGL vendor/renderer
            const getParameterProxyHandler = {
                apply: function(target, thisArg, args) {
                    const param = args[0];
                    const gl = thisArg;
                    if (param === 37445) return 'Intel Inc.';
                    if (param === 37446) return 'Intel Iris OpenGL Engine';
                    return Reflect.apply(target, thisArg, args);
                }
            };
            try {
                const canvas = document.createElement('canvas');
                const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
                if (gl) {
                    const originalGetParameter = gl.getParameter.bind(gl);
                    gl.getParameter = new Proxy(originalGetParameter, getParameterProxyHandler);
                }
            } catch(e) {}

            // Randomize canvas fingerprint slightly
            const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
            HTMLCanvasElement.prototype.toDataURL = function(type) {
                if (type === 'image/png' && this.width > 16 && this.height > 16) {
                    const context = this.getContext('2d');
                    if (context) {
                        const imageData = context.getImageData(0, 0, this.width, this.height);
                        for (let i = 0; i < imageData.data.length; i += 100) {
                            imageData.data[i] = imageData.data[i] ^ (Math.random() > 0.5 ? 1 : 0);
                        }
                        context.putImageData(imageData, 0, 0);
                    }
                }
                return originalToDataURL.apply(this, arguments);
            };

            // Spoof connection info
            if (navigator.connection) {
                Object.defineProperty(navigator.connection, 'effectiveType', { get: () => '4g' });
                Object.defineProperty(navigator.connection, 'downlink', { get: () => 10 });
                Object.defineProperty(navigator.connection, 'rtt', { get: () => 50 });
            }

            console.log("Anti-detection scripts loaded");
        '''

    async def _setup_request_interception(self):
        """Block known fingerprinting and tracking scripts"""
        blocked_resources = [
            'datadome.co',
            'fingerprintjs',
            'fpjs.io',
            'botd.min.js',
            'sentry.io',
            'segment.io',
            'amplitude.com',
            'mixpanel.com',
        ]

        async def handle_route(route):
            url = route.request.url.lower()
            if any(blocked in url for blocked in blocked_resources):
                await route.abort()
            else:
                await route.continue_()

        try:
            await self.page.route('**/*', handle_route)
        except Exception as e:
            logger.debug(f"Could not set up request interception: {e}")

    async def _extract_youtube_channels_from_google(self, query: str, max_results: int = 20) -> List[Dict]:
        """Search Google and extract YouTube channel links"""
        channels = []

        try:
            # Construct Google search URL
            search_url = f"https://www.google.com/search?q={quote_plus(query)}&num={max_results}"

            await self.page.goto(search_url, timeout=30000, wait_until='domcontentloaded')
            await asyncio.sleep(2)

            # Accept cookies if prompted
            try:
                accept_btn = await self.page.query_selector('button:has-text("Accept all"), button:has-text("I agree")')
                if accept_btn:
                    await accept_btn.click()
                    await asyncio.sleep(1)
            except:
                pass

            # Extract all links
            links = await self.page.query_selector_all('a[href*="youtube.com"]')

            for link in links:
                try:
                    href = await link.get_attribute('href')
                    if not href:
                        continue

                    # Extract YouTube channel URL
                    channel_info = self._parse_youtube_url(href)
                    if channel_info and channel_info['channel_id'] not in self.discovered_channels:
                        self.discovered_channels.add(channel_info['channel_id'])
                        channels.append(channel_info)

                except Exception as e:
                    continue

            logger.info(f"Found {len(channels)} new channels for query: {query}")

        except Exception as e:
            logger.error(f"Error searching Google: {e}")

        return channels

    async def _search_youtube_directly(self, query: str, max_results: int = 20) -> List[Dict]:
        """Search YouTube directly for channels"""
        channels = []

        try:
            # Search on YouTube
            search_url = f"https://www.youtube.com/results?search_query={quote_plus(query)}&sp=EgIQAg%253D%253D"  # sp filter for channels

            await self.page.goto(search_url, timeout=30000, wait_until='domcontentloaded')
            await asyncio.sleep(random.uniform(2, 4))  # Random delay for human-like behavior

            # Simulate human behavior - random mouse movement
            await self._simulate_human_behavior()

            # Accept cookies if prompted
            try:
                accept_btn = await self.page.query_selector('button[aria-label*="Accept"], tp-yt-paper-button:has-text("Accept all")')
                if accept_btn:
                    await accept_btn.click()
                    await asyncio.sleep(1)
            except:
                pass

            # Wait for results
            await self.page.wait_for_selector('ytd-channel-renderer, ytd-search-result-renderer', timeout=10000)

            # Scroll down to load more results
            await self._scroll_page()

            # Extract channel results
            channel_elements = await self.page.query_selector_all('ytd-channel-renderer')

            for element in channel_elements[:max_results]:
                try:
                    # Get channel link
                    link = await element.query_selector('a#main-link, a.channel-link')
                    if not link:
                        continue

                    href = await link.get_attribute('href')
                    if not href:
                        continue

                    # Get channel name - clean up duplicates
                    name_el = await element.query_selector('#channel-title, yt-formatted-string#text')
                    channel_name = await name_el.text_content() if name_el else None
                    if channel_name:
                        channel_name = self._clean_channel_name(channel_name)

                    # Get subscriber count
                    sub_el = await element.query_selector('#subscribers, #video-count')
                    sub_text = await sub_el.text_content() if sub_el else None

                    channel_info = self._parse_youtube_url(f"https://www.youtube.com{href}")
                    if channel_info and channel_info['channel_id'] not in self.discovered_channels:
                        channel_info['channel_name'] = channel_name
                        channel_info['subscriber_hint'] = sub_text.strip() if sub_text else None
                        self.discovered_channels.add(channel_info['channel_id'])
                        channels.append(channel_info)

                except Exception as e:
                    continue

            logger.info(f"Found {len(channels)} channels from YouTube search: {query}")

        except Exception as e:
            logger.error(f"Error searching YouTube: {e}")

        return channels

    def _clean_channel_name(self, name: str) -> str:
        """Clean channel name by removing duplicates and extra whitespace"""
        if not name:
            return name

        # Remove excessive whitespace and newlines
        name = ' '.join(name.split())

        # Check for duplicated names (e.g., "Tech Channel  Tech Channel")
        words = name.split()
        if len(words) >= 2:
            half = len(words) // 2
            first_half = ' '.join(words[:half])
            second_half = ' '.join(words[half:])
            if first_half == second_half:
                return first_half

        return name.strip()

    async def _simulate_human_behavior(self):
        """Simulate human-like mouse movements and delays"""
        try:
            # Random mouse movements
            viewport = {'width': 1920, 'height': 1080}
            for _ in range(random.randint(2, 4)):
                x = random.randint(100, viewport['width'] - 100)
                y = random.randint(100, viewport['height'] - 100)
                await self.page.mouse.move(x, y)
                await asyncio.sleep(random.uniform(0.05, 0.15))
        except Exception as e:
            logger.debug(f"Human behavior simulation: {e}")

    async def _scroll_page(self):
        """Scroll page to load more results"""
        try:
            for _ in range(random.randint(2, 4)):
                scroll_amount = random.randint(200, 500)
                await self.page.evaluate(f'window.scrollBy(0, {scroll_amount})')
                await asyncio.sleep(random.uniform(0.3, 0.8))
        except Exception as e:
            logger.debug(f"Scroll error: {e}")

    def _parse_youtube_url(self, url: str) -> Optional[Dict]:
        """Parse YouTube URL and extract channel identifier"""
        if not url:
            return None

        # Handle Google redirect URLs
        if 'google.com' in url and 'url=' in url:
            parsed = urlparse(url)
            query_params = parse_qs(parsed.query)
            if 'url' in query_params:
                url = query_params['url'][0]

        # Parse YouTube URLs
        patterns = [
            (r'youtube\.com/@([^/\?&]+)', 'handle'),           # @handle format
            (r'youtube\.com/channel/([^/\?&]+)', 'channel_id'), # Channel ID format
            (r'youtube\.com/c/([^/\?&]+)', 'custom_url'),       # Custom URL format
            (r'youtube\.com/user/([^/\?&]+)', 'user'),          # Legacy user format
        ]

        for pattern, url_type in patterns:
            match = re.search(pattern, url, re.IGNORECASE)
            if match:
                identifier = match.group(1)
                return {
                    'channel_id': identifier,
                    'url_type': url_type,
                    'original_url': url,
                }

        return None

    def _get_progress_file(self, session_id: str = None) -> Path:
        """Get progress file path for current session"""
        if session_id:
            filename = f"discovery_progress_{session_id}.json"
        else:
            region_str = self.region if self.region else 'default'
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"discovery_progress_{region_str}_{timestamp}.json"
        return self.progress_dir / filename

    def _load_progress(self, progress_file: str) -> Dict:
        """Load discovery progress from file"""
        try:
            with open(progress_file, 'r', encoding='utf-8') as f:
                progress = json.load(f)
                # Restore discovered channels set
                self.discovered_channels = set(progress.get('discovered_channel_ids', []))
                logger.info(f"Loaded progress: {len(self.discovered_channels)} channels already discovered")
                return progress
        except Exception as e:
            logger.error(f"Could not load progress file: {e}")
            return None

    def _save_progress(self, progress_data: Dict, progress_file: Path):
        """Save discovery progress to file"""
        try:
            progress_data['discovered_channel_ids'] = list(self.discovered_channels)
            progress_data['last_updated'] = datetime.now(timezone.utc).isoformat()
            
            with open(progress_file, 'w', encoding='utf-8') as f:
                json.dump(progress_data, f, indent=2, ensure_ascii=False)
            logger.debug(f"Progress saved to {progress_file}")
        except Exception as e:
            logger.error(f"Failed to save progress: {e}")

    async def discover_channels(
        self,
        categories: List[str] = None,
        locations: List[str] = None,
        search_terms: List[str] = None,
        max_per_search: int = 20,
        use_youtube_search: bool = True,
        resume: bool = False,
        progress_file: str = None
    ) -> List[Dict]:
        """
        Discover YouTube channels by category and location

        Args:
            categories: List of content categories (e.g., 'tech', 'beauty')
            locations: List of locations (e.g., 'India', 'Mumbai')
            search_terms: Custom search terms
            max_per_search: Max results per search query
            use_youtube_search: Also search on YouTube directly
            resume: Resume from previous progress file
            progress_file: Specific progress file to resume from

        Returns:
            List of discovered channel info dicts
        """
        categories = categories or self.config.get('categories', ['tech'])
        locations = locations or self.config.get('locations', ['India'])

        all_channels = []
        
        # Initialize or load progress
        if resume and progress_file:
            progress = self._load_progress(progress_file)
            if progress:
                start_index = progress.get('current_query_index', 0)
                completed_queries = set(progress.get('completed_queries', []))
                failed_queries = progress.get('failed_queries', {})
                all_channels = progress.get('discovered_channels', [])
                session_id = progress.get('session_id')
                progress_path = Path(progress_file)
            else:
                start_index = 0
                completed_queries = set()
                failed_queries = {}
                session_id = datetime.now().strftime('%Y%m%d_%H%M%S')
                progress_path = self._get_progress_file(session_id)
        else:
            start_index = 0
            completed_queries = set()
            failed_queries = {}
            session_id = datetime.now().strftime('%Y%m%d_%H%M%S')
            progress_path = self._get_progress_file(session_id)

        # Build search queries
        queries = []

        if search_terms:
            queries.extend(search_terms)
        else:
            for category in categories:
                for location in locations:
                    # Various query formats
                    queries.extend([
                        f"{location} {category} influencer",
                    ])

        logger.info(f"Running {len(queries)} search queries (starting from index {start_index})...")
        
        # Initialize progress data
        progress_data = {
            'session_id': session_id,
            'region': self.region,
            'config_path': str(self.config_path),
            'total_queries': len(queries),
            'current_query_index': start_index,
            'completed_queries': list(completed_queries),
            'failed_queries': failed_queries,
            'discovered_channels': all_channels,
            'categories': categories,
            'locations': locations,
            'started_at': datetime.now(timezone.utc).isoformat()
        }

        try:
            for i in range(start_index, len(queries)):
                query = queries[i]
                
                # Skip if already completed
                if query in completed_queries:
                    logger.info(f"[{i+1}/{len(queries)}] Skipping completed query: {query}")
                    continue
                
                logger.info(f"[{i+1}/{len(queries)}] Searching: {query}")

                try:
                    # Search YouTube directly
                    if use_youtube_search:
                        yt_channels = await self._search_youtube_directly(query, max_per_search)
                        all_channels.extend(yt_channels)
                    
                    # Mark query as completed
                    completed_queries.add(query)
                    progress_data['current_query_index'] = i + 1
                    progress_data['completed_queries'] = list(completed_queries)
                    progress_data['discovered_channels'] = all_channels
                    
                    # Save progress after each query
                    self._save_progress(progress_data, progress_path)
                    
                except Exception as e:
                    logger.error(f"Error searching '{query}': {e}")
                    failed_queries[query] = failed_queries.get(query, 0) + 1
                    progress_data['failed_queries'] = failed_queries
                    self._save_progress(progress_data, progress_path)

                # Random delay between searches
                delay_range = self.config.get('search_delay', (3, 7))
                delay = random.uniform(*delay_range)
                logger.info(f"Waiting {delay:.1f}s before next search...")
                await asyncio.sleep(delay)

        except KeyboardInterrupt:
            logger.warning("Discovery interrupted by user. Progress saved.")
            self._save_progress(progress_data, progress_path)
            raise
        except Exception as e:
            logger.error(f"Discovery error: {e}")
            self._save_progress(progress_data, progress_path)
            raise

        # Mark as completed
        progress_data['completed'] = True
        progress_data['completed_at'] = datetime.now(timezone.utc).isoformat()
        self._save_progress(progress_data, progress_path)
        
        logger.info(f"Total unique channels discovered: {len(self.discovered_channels)}")
        logger.info(f"Progress saved to: {progress_path}")
        return all_channels

    def create_queue_file(
        self,
        channels: List[Dict],
        category: str = None,
        location: str = None,
        filename: str = None
    ) -> str:
        """Create a queue file for the scraper"""
        if not filename:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            cat_str = category.replace(' ', '_') if category else 'mixed'
            loc_str = location.replace(' ', '_') if location else 'global'
            region_str = f"_{self.region}" if self.region else ''
            filename = f"{cat_str}_{loc_str}{region_str}_{timestamp}.json"

        filepath = self.queue_dir / filename

        queue_data = {
            'category': category,
            'location': location,
            'channels': [ch['channel_id'] for ch in channels],
            'channel_details': channels,
            'completed': [],
            'failed': {},
            'current_index': 0,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'total_count': len(channels)
        }

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(queue_data, f, indent=2, ensure_ascii=False)

        logger.info(f"Created queue file: {filepath} with {len(channels)} channels")
        return str(filepath)

    async def cleanup(self):
        """Close browser"""
        if self.page:
            await self.page.close()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if hasattr(self, 'playwright') and self.playwright:
            await self.playwright.stop()
        logger.info("Discovery browser cleanup complete")


async def discover_and_create_queue(
    categories: List[str] = None,
    locations: List[str] = None,
    search_terms: List[str] = None,
    headless: bool = True,
    output_file: str = None,
    config_path: str = None,
    resume: bool = False,
    progress_file: str = None
) -> str:
    """Convenience function to discover channels and create a queue file"""
    discovery = YouTubeChannelDiscovery(config_path=config_path)
    await discovery.start_browser(headless=headless)

    try:
        channels = await discovery.discover_channels(
            categories=categories,
            locations=locations,
            search_terms=search_terms,
            resume=resume,
            progress_file=progress_file
        )

        if channels:
            category = categories[0] if categories and len(categories) == 1 else None
            location = locations[0] if locations and len(locations) == 1 else None
            queue_file = discovery.create_queue_file(
                channels,
                category=category,
                location=location,
                filename=output_file
            )
            return queue_file
        else:
            logger.warning("No channels discovered")
            return None

    finally:
        await discovery.cleanup()


def main():
    """Main entry point for CLI usage"""
    import argparse

    parser = argparse.ArgumentParser(description='YouTube Channel Discovery')
    parser.add_argument('--categories', '-c', nargs='+', help='Content categories to search')
    parser.add_argument('--locations', '-l', nargs='+', help='Locations to search')
    parser.add_argument('--search', '-s', nargs='+', help='Custom search terms')
    parser.add_argument('--output', '-o', help='Output queue filename')
    parser.add_argument('--config', help='Path to region-specific config file (e.g., config/scraper_config_us.json)')
    parser.add_argument('--resume', action='store_true', help='Resume from previous discovery session')
    parser.add_argument('--progress-file', help='Specific progress file to resume from')
    parser.add_argument('--headless', action='store_true', default=True, help='Run in headless mode')
    parser.add_argument('--no-headless', action='store_false', dest='headless', help='Show browser')

    args = parser.parse_args()

    if not args.categories and not args.locations and not args.search and not args.resume:
        # Default discovery
        args.categories = ['tech', 'gaming']
        args.locations = ['India']

    queue_file = asyncio.run(discover_and_create_queue(
        categories=args.categories,
        locations=args.locations,
        search_terms=args.search,
        headless=args.headless,
        output_file=args.output,
        config_path=args.config,
        resume=args.resume,
        progress_file=args.progress_file
    ))

    if queue_file:
        print(f"\nQueue file created: {queue_file}")
        print(f"\nTo scrape these channels, run:")
        print(f"  python youtube_channel_scraper.py --queue {queue_file}")


if __name__ == '__main__':
    main()
