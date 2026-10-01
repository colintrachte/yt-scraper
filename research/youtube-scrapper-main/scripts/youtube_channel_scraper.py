#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube Channel Scraper with Playwright Browser Automation
No login required - scrapes public channel data
Includes advanced anti-detection techniques
"""

import asyncio
import json
import os
import sys
import logging
import time
import re
from typing import List, Dict, Optional, Tuple
from pathlib import Path
from playwright.async_api import async_playwright, Page, Browser
from datetime import datetime, timezone
import random
from dotenv import load_dotenv
import aiohttp
import hashlib
from PIL import Image
import io

# Import shared anti-detection module
try:
    from anti_detection import AntiDetectionManager, get_stealth_scripts, HumanBehavior
except ImportError:
    # Fallback if module not found
    AntiDetectionManager = None
    get_stealth_scripts = None
    HumanBehavior = None

# Import proxy manager
try:
    from proxy_manager import ProxyManager
except ImportError:
    ProxyManager = None

# Set UTF-8 encoding for stdout to handle emoji characters
if sys.platform == 'win32':
    try:
        import codecs
        if hasattr(sys.stdout, 'buffer'):
            sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
        if hasattr(sys.stderr, 'buffer'):
            sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')
    except Exception:
        pass  # Already wrapped or not available

# Load environment variables from .env file
load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Global variable to track current region for progress file naming
CURRENT_REGION = None

# Global error tracking for consecutive similar errors
ERROR_TRACKER = {
    'last_error_type': None,
    'consecutive_count': 0,
    'cooldown_level': 0
}


class ChannelSkippedException(Exception):
    """Exception raised when a channel should be skipped (already exists)"""
    pass


class ChannelNotFoundException(Exception):
    """Exception raised when a channel doesn't exist or is unavailable"""
    pass


class RateLimitException(Exception):
    """Exception raised when YouTube rate limits the request"""
    pass


def parse_subscriber_count(text: str) -> int:
    """Parse subscriber count from text like '1.5M subscribers' or '500K subscribers'"""
    if not text:
        return 0

    text = text.lower().replace(',', '').replace(' ', '')
    text = text.replace('subscribers', '').replace('subscriber', '').strip()

    try:
        if 'k' in text:
            return int(float(text.replace('k', '')) * 1000)
        elif 'm' in text:
            return int(float(text.replace('m', '')) * 1000000)
        elif 'b' in text:
            return int(float(text.replace('b', '')) * 1000000000)
        else:
            return int(float(text))
    except (ValueError, TypeError):
        return 0


def parse_view_count(text: str) -> int:
    """Parse view count from text like '1.5M views' or '500K views'"""
    if not text:
        return 0

    text = text.lower().replace(',', '').replace(' ', '')
    text = text.replace('views', '').replace('view', '').strip()

    try:
        if 'k' in text:
            return int(float(text.replace('k', '')) * 1000)
        elif 'm' in text:
            return int(float(text.replace('m', '')) * 1000000)
        elif 'b' in text:
            return int(float(text.replace('b', '')) * 1000000000)
        else:
            return int(float(text))
    except (ValueError, TypeError):
        return 0


def parse_video_count(text: str) -> int:
    """Parse video count from text like '450 videos'"""
    if not text:
        return 0

    text = text.lower().replace(',', '').replace(' ', '')
    text = text.replace('videos', '').replace('video', '').strip()

    try:
        if 'k' in text:
            return int(float(text.replace('k', '')) * 1000)
        else:
            return int(float(text))
    except (ValueError, TypeError):
        return 0


def determine_influencer_tier(subscribers: int) -> str:
    """Determine influencer tier based on subscriber count"""
    if subscribers >= 1000000:
        return "mega"
    elif subscribers >= 100000:
        return "macro"
    elif subscribers >= 10000:
        return "mid"
    elif subscribers >= 1000:
        return "micro"
    else:
        return "nano"


def _detect_region_from_path(path: str) -> Optional[str]:
    """Detect region from file path"""
    if not path:
        return None
    path_lower = path.lower()
    for region in ['us', 'uk', 'eur', 'east', 'gulf', 'ind']:
        if f'_{region}' in path_lower or f'/{region}/' in path_lower or f'\\{region}\\' in path_lower:
            return region
    return None


def get_output_dir(queue_file: str = None, config_path: str = None) -> Path:
    """Get region-specific output directory"""
    base = Path(__file__).parent / 'data'
    
    # Try to detect region from queue file or config path
    region = _detect_region_from_path(queue_file) or _detect_region_from_path(config_path)
    
    if region:
        return base / f'output_{region}'
    return base / 'output'


def get_thumbnails_dir(queue_file: str = None, config_path: str = None) -> Path:
    """Get region-specific thumbnails directory"""
    base = Path(__file__).parent
    
    # Try to detect region from queue file or config path
    region = _detect_region_from_path(queue_file) or _detect_region_from_path(config_path)
    
    if region:
        return base / f'thumbnails_{region}'
    return base / 'thumbnails'


class YouTubeScraperPlaywright:
    """YouTube channel scraper using Playwright for browser automation"""

    def __init__(self, config_path: str = None, queue_file: str = None):
        if config_path is None:
            config_path = Path(__file__).parent / 'config' / 'scraper_config.json'
        self.config_path = str(config_path)
        self.config = self._load_config(self.config_path)
        self.browser = None
        self.context = None
        self.page = None
        self.queue_file = queue_file

        # Initialize proxy manager
        self.proxy_manager = self._init_proxy_manager()

        # Setup directories (region-specific if queue_file or config_path provided)
        self.output_dir = get_output_dir(queue_file, self.config_path)
        self.thumbnails_dir = get_thumbnails_dir(queue_file, self.config_path)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.thumbnails_dir.mkdir(parents=True, exist_ok=True)

    def _load_config(self, config_path: str) -> Dict:
        """Load configuration from JSON file"""
        try:
            with open(config_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load config: {e}. Using defaults.")
            return {
                'categories': ['gaming', 'tech', 'beauty', 'fitness', 'food', 'travel', 'music', 'education'],
                'max_videos_to_scrape': 6,
                'headless': True
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
        pm = ProxyManager.from_config(self.config_path)
        if pm.enabled:
            logger.info(f"Proxy loaded from config: {pm}")
            return pm

        logger.debug("No proxy configured — running without proxy")
        return pm

    async def start_browser(self, headless: bool = True):
        """Start Playwright browser with advanced anti-detection measures"""
        logger.info("Starting browser with anti-detection...")
        self.playwright = await async_playwright().start()

        # Get anti-detection configuration
        if AntiDetectionManager:
            self.anti_detection = AntiDetectionManager()
            fingerprint = self.anti_detection.generate_fingerprint()
            chrome_args = self.anti_detection.get_chrome_args()
        else:
            self.anti_detection = None
            fingerprint = self._get_default_fingerprint()
            chrome_args = self._get_default_chrome_args()

        # Store fingerprint for later use
        self.current_fingerprint = fingerprint

        # Launch browser with anti-detection settings
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

        # Create context with realistic fingerprint (and optional proxy)
        context_kwargs = dict(
            viewport=fingerprint['viewport'],
            user_agent=fingerprint['user_agent'],
            locale=fingerprint['locale'],
            timezone_id=fingerprint['timezone'],
            color_scheme=fingerprint.get('color_scheme', 'light'),
            device_scale_factor=fingerprint.get('device_scale_factor', 1),
            has_touch=fingerprint.get('has_touch', False),
            java_script_enabled=True,
            bypass_csp=True,  # Bypass Content Security Policy for script injection
        )
        if proxy_config:
            context_kwargs['proxy'] = proxy_config

        self.context = await self.browser.new_context(**context_kwargs)

        # Create page
        self.page = await self.context.new_page()

        # Inject comprehensive stealth scripts
        if get_stealth_scripts:
            stealth_js = get_stealth_scripts(fingerprint)
        else:
            stealth_js = self._get_default_stealth_script()

        await self.page.add_init_script(stealth_js)

        # Block fingerprinting scripts
        await self._setup_request_interception()

        # Initialize human behavior simulator
        if HumanBehavior:
            self.human_behavior = HumanBehavior(self.page)
        else:
            self.human_behavior = None

        logger.info(f"Browser started with UA: {fingerprint['user_agent'][:60]}...")

    def _get_default_fingerprint(self) -> Dict:
        """Generate a realistic browser fingerprint"""
        # Common user agents from real browser statistics
        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
        ]

        # Common viewport sizes from real browser statistics
        viewports = [
            {'width': 1920, 'height': 1080},  # Most common
            {'width': 1366, 'height': 768},
            {'width': 1536, 'height': 864},
            {'width': 1440, 'height': 900},
            {'width': 1680, 'height': 1050},
            {'width': 2560, 'height': 1440},
            {'width': 1280, 'height': 720},
        ]

        timezones = [
            'America/New_York', 'America/Chicago', 'America/Los_Angeles',
            'America/Denver', 'Europe/London', 'Europe/Paris',
            'Asia/Kolkata', 'Asia/Tokyo', 'Asia/Singapore',
            'Australia/Sydney', 'Pacific/Auckland'
        ]

        locales = ['en-US', 'en-GB', 'en-IN', 'en-AU', 'en-CA']

        return {
            'user_agent': random.choice(user_agents),
            'viewport': random.choice(viewports),
            'timezone': random.choice(timezones),
            'locale': random.choice(locales),
            'color_scheme': random.choice(['light', 'dark']),
            'device_scale_factor': random.choice([1, 1.25, 1.5, 2]),
            'has_touch': False,
            'platform': 'Win32' if 'Windows' in random.choice(user_agents) else 'MacIntel',
            'hardware_concurrency': random.choice([4, 6, 8, 12, 16]),
            'device_memory': random.choice([4, 8, 16, 32]),
        }

    def _get_default_chrome_args(self) -> List[str]:
        """Get Chrome launch arguments for anti-detection"""
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
            '--disable-features=IsolateOrigins,site-per-process',
            '--disable-ipc-flooding-protection',
            '--window-size=1920,1080',
            '--start-maximized',
            '--no-first-run',
            '--no-default-browser-check',
            '--disable-gpu',
            '--disable-accelerated-2d-canvas',
            '--disable-web-security',
            '--allow-running-insecure-content',
            '--ignore-certificate-errors',
        ]

    def _get_default_stealth_script(self) -> str:
        """Get comprehensive JavaScript for hiding automation"""
        return '''
            // ============ WEBDRIVER DETECTION ============
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
                configurable: true
            });

            // Delete webdriver from prototype
            delete navigator.__proto__.webdriver;

            // ============ CHROME RUNTIME ============
            window.chrome = {
                runtime: {
                    id: undefined,
                    connect: () => {},
                    sendMessage: () => {},
                    onMessage: { addListener: () => {}, removeListener: () => {} },
                    onConnect: { addListener: () => {}, removeListener: () => {} },
                },
                loadTimes: function() {
                    return {
                        commitLoadTime: Date.now() / 1000 - Math.random() * 5,
                        connectionInfo: "h2",
                        finishDocumentLoadTime: Date.now() / 1000 - Math.random(),
                        finishLoadTime: Date.now() / 1000 - Math.random() * 0.5,
                        firstPaintAfterLoadTime: 0,
                        firstPaintTime: Date.now() / 1000 - Math.random() * 3,
                        navigationType: "Other",
                        npnNegotiatedProtocol: "unknown",
                        requestTime: Date.now() / 1000 - Math.random() * 10,
                        startLoadTime: Date.now() / 1000 - Math.random() * 8,
                        wasAlternateProtocolAvailable: false,
                        wasFetchedViaSpdy: true,
                        wasNpnNegotiated: true
                    };
                },
                csi: function() {
                    return {
                        onloadT: Date.now(),
                        pageT: Math.random() * 1000 + 500,
                        startE: Date.now() - Math.random() * 10000,
                        tran: 15
                    };
                },
            };

            // ============ PERMISSIONS API ============
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => {
                if (parameters.name === 'notifications') {
                    return Promise.resolve({ state: Notification.permission });
                }
                if (parameters.name === 'midi' || parameters.name === 'camera' || parameters.name === 'microphone') {
                    return Promise.resolve({ state: 'prompt' });
                }
                return originalQuery.call(navigator.permissions, parameters);
            };

            // ============ PLUGINS ============
            Object.defineProperty(navigator, 'plugins', {
                get: () => {
                    const pluginArray = [
                        {
                            0: { type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: 'Portable Document Format', enabledPlugin: null },
                            name: 'Chrome PDF Plugin',
                            filename: 'internal-pdf-viewer',
                            description: 'Portable Document Format',
                            length: 1
                        },
                        {
                            0: { type: 'application/pdf', suffixes: 'pdf', description: '', enabledPlugin: null },
                            name: 'Chrome PDF Viewer',
                            filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai',
                            description: '',
                            length: 1
                        },
                        {
                            0: { type: 'application/x-nacl', suffixes: '', description: 'Native Client Executable', enabledPlugin: null },
                            1: { type: 'application/x-pnacl', suffixes: '', description: 'Portable Native Client Executable', enabledPlugin: null },
                            name: 'Native Client',
                            filename: 'internal-nacl-plugin',
                            description: '',
                            length: 2
                        }
                    ];
                    pluginArray.item = (i) => pluginArray[i];
                    pluginArray.namedItem = (name) => pluginArray.find(p => p.name === name);
                    pluginArray.refresh = () => {};
                    return pluginArray;
                },
            });

            // ============ LANGUAGES ============
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en', 'hi'],
            });

            // ============ PLATFORM ============
            Object.defineProperty(navigator, 'platform', {
                get: () => 'Win32',
            });

            // ============ HARDWARE ============
            Object.defineProperty(navigator, 'hardwareConcurrency', {
                get: () => 8,
            });

            Object.defineProperty(navigator, 'deviceMemory', {
                get: () => 8,
            });

            Object.defineProperty(navigator, 'maxTouchPoints', {
                get: () => 0,
            });

            // ============ CONNECTION ============
            if (navigator.connection) {
                Object.defineProperty(navigator.connection, 'effectiveType', { get: () => '4g' });
                Object.defineProperty(navigator.connection, 'downlink', { get: () => 10 + Math.random() * 5 });
                Object.defineProperty(navigator.connection, 'rtt', { get: () => 50 + Math.floor(Math.random() * 50) });
                Object.defineProperty(navigator.connection, 'saveData', { get: () => false });
            }

            // ============ WEBGL ============
            const getParameterProxyHandler = {
                apply: function(target, thisArg, args) {
                    const param = args[0];
                    // UNMASKED_VENDOR_WEBGL
                    if (param === 37445) return 'Google Inc. (Intel)';
                    // UNMASKED_RENDERER_WEBGL
                    if (param === 37446) return 'ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)';
                    return Reflect.apply(target, thisArg, args);
                }
            };

            try {
                ['getParameter'].forEach(name => {
                    const canvas = document.createElement('canvas');
                    ['webgl', 'experimental-webgl', 'webgl2'].forEach(ctxName => {
                        try {
                            const ctx = canvas.getContext(ctxName);
                            if (ctx) {
                                ctx[name] = new Proxy(ctx[name].bind(ctx), getParameterProxyHandler);
                            }
                        } catch(e) {}
                    });
                });
            } catch(e) {}

            // ============ CANVAS FINGERPRINT NOISE ============
            const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
            const originalGetImageData = CanvasRenderingContext2D.prototype.getImageData;

            HTMLCanvasElement.prototype.toDataURL = function(type) {
                if (type === 'image/png' && this.width > 16 && this.height > 16) {
                    try {
                        const context = this.getContext('2d');
                        if (context) {
                            const imageData = originalGetImageData.call(context, 0, 0, this.width, this.height);
                            const noise = Math.floor(Math.random() * 10) - 5;
                            for (let i = 0; i < imageData.data.length; i += 4) {
                                if (Math.random() < 0.001) {
                                    imageData.data[i] = Math.max(0, Math.min(255, imageData.data[i] + noise));
                                }
                            }
                            context.putImageData(imageData, 0, 0);
                        }
                    } catch(e) {}
                }
                return originalToDataURL.apply(this, arguments);
            };

            // ============ AUDIO CONTEXT ============
            if (window.AudioContext || window.webkitAudioContext) {
                const AudioContextClass = window.AudioContext || window.webkitAudioContext;
                const originalCreateOscillator = AudioContextClass.prototype.createOscillator;
                AudioContextClass.prototype.createOscillator = function() {
                    const oscillator = originalCreateOscillator.apply(this, arguments);
                    return oscillator;
                };
            }

            // ============ IFRAME DETECTION ============
            Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
                get: function() {
                    const contentWindow = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow').get.call(this);
                    if (contentWindow) {
                        try {
                            contentWindow.chrome = window.chrome;
                        } catch(e) {}
                    }
                    return contentWindow;
                }
            });

            // ============ NOTIFICATION ============
            if (!window.Notification) {
                window.Notification = {
                    permission: 'default',
                    requestPermission: () => Promise.resolve('default'),
                };
            }

            // ============ MEDIA DEVICES ============
            if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {
                const originalEnumerateDevices = navigator.mediaDevices.enumerateDevices.bind(navigator.mediaDevices);
                navigator.mediaDevices.enumerateDevices = () => {
                    return originalEnumerateDevices().then(devices => {
                        // Return generic device list
                        return [
                            { deviceId: 'default', groupId: '', kind: 'audioinput', label: '' },
                            { deviceId: 'default', groupId: '', kind: 'audiooutput', label: '' },
                            { deviceId: 'default', groupId: '', kind: 'videoinput', label: '' },
                        ];
                    });
                };
            }

            // ============ DATE TIMEZONE ============
            const originalGetTimezoneOffset = Date.prototype.getTimezoneOffset;
            Date.prototype.getTimezoneOffset = function() {
                return originalGetTimezoneOffset.call(this);
            };

            console.log("[Stealth] Anti-detection scripts loaded successfully");
        '''

    async def _setup_request_interception(self):
        """Block known fingerprinting and bot detection scripts"""
        blocked_patterns = [
            '*datadome.co*',
            '*fingerprintjs*',
            '*fpjs.io*',
            '*botd*',
            '*sentry.io*',
            '*segment.io*',
            '*amplitude.com*',
            '*mixpanel.com*',
            '*perimeterx*',
            '*px-cdn*',
            '*kasada*',
            '*distilnetworks*',
            '*akamaihd.net/*/tpm/*',
            '*imperva*',
            '*cloudflare*/cdn-cgi/bm/*',
        ]

        async def handle_route(route):
            url = route.request.url.lower()
            for pattern in blocked_patterns:
                pattern_clean = pattern.replace('*', '')
                if pattern_clean and pattern_clean in url:
                    logger.debug(f"Blocked fingerprinting request: {url[:80]}")
                    await route.abort()
                    return
            await route.continue_()

        try:
            await self.page.route('**/*', handle_route)
        except Exception as e:
            logger.debug(f"Request interception setup: {e}")

    async def simulate_human_behavior(self):
        """Simulate human-like mouse movements and scrolling"""
        try:
            if self.human_behavior:
                await self.human_behavior.random_mouse_movement()
                await self.human_behavior.random_scroll()
            else:
                # Fallback basic human simulation
                await self._basic_human_simulation()
        except Exception as e:
            logger.debug(f"Human behavior simulation: {e}")

    async def _basic_human_simulation(self):
        """Basic human behavior simulation"""
        try:
            # Random mouse movements
            viewport = self.current_fingerprint.get('viewport', {'width': 1920, 'height': 1080})
            for _ in range(random.randint(2, 5)):
                x = random.randint(100, viewport['width'] - 100)
                y = random.randint(100, viewport['height'] - 100)
                await self.page.mouse.move(x, y)
                await asyncio.sleep(random.uniform(0.1, 0.3))

            # Random scroll
            scroll_amount = random.randint(100, 500)
            await self.page.evaluate(f'window.scrollBy(0, {scroll_amount})')
            await asyncio.sleep(random.uniform(0.5, 1.5))

            # Scroll back up slightly
            scroll_back = random.randint(50, 150)
            await self.page.evaluate(f'window.scrollBy(0, -{scroll_back})')

        except Exception as e:
            pass  # Silently fail - not critical

        logger.info("Browser started successfully")

    async def download_image(self, url: str, channel_id: str, image_type: str = 'profile', video_id: str = None) -> Optional[str]:
        """Download and save image locally

        Args:
            url: Image URL to download
            channel_id: Channel identifier for folder organization
            image_type: Type of image ('profile', 'banner', or 'video')
            video_id: YouTube video ID (used as filename for video thumbnails)

        Returns:
            Local path relative to thumbnails directory, or None if failed
        """
        if not url:
            return None

        try:
            # Create channel thumbnail directory
            safe_channel_id = channel_id.replace('@', '').replace('/', '_').replace('\\', '_')
            channel_dir = self.thumbnails_dir / safe_channel_id
            channel_dir.mkdir(parents=True, exist_ok=True)

            # Generate filename based on image type
            if image_type == 'profile':
                url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
                filename = f"profile_{url_hash}.jpg"
            elif image_type == 'banner':
                url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
                filename = f"banner_{url_hash}.jpg"
            else:
                # Use video_id as filename for video thumbnails
                if video_id:
                    filename = f"{video_id}.jpg"
                else:
                    url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
                    filename = f"video_{url_hash}.jpg"

            filepath = channel_dir / filename

            # Skip if already exists
            if filepath.exists():
                return str(filepath.relative_to(self.thumbnails_dir.parent))

            # Download image
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                    if response.status == 200:
                        image_data = await response.read()

                        # Resize image to reduce storage
                        try:
                            img = Image.open(io.BytesIO(image_data))
                            if img.mode in ('RGBA', 'P'):
                                img = img.convert('RGB')

                            # Resize to reasonable dimensions
                            max_size = (400, 400) if image_type == 'profile' else (640, 360)
                            img.thumbnail(max_size, Image.Resampling.LANCZOS)

                            # Save with compression
                            img.save(filepath, 'JPEG', quality=85, optimize=True)
                            logger.debug(f"Downloaded {image_type} image for {channel_id}")
                            return str(filepath.relative_to(self.thumbnails_dir.parent))
                        except Exception as e:
                            logger.warning(f"Failed to process image: {e}")
                            # Save raw if processing fails
                            with open(filepath, 'wb') as f:
                                f.write(image_data)
                            return str(filepath.relative_to(self.thumbnails_dir.parent))

        except Exception as e:
            logger.warning(f"Failed to download image {url}: {e}")
            return None

    async def _extract_channel_info(self) -> Dict:
        """Extract channel information from the current page"""
        info = {
            'channel_name': None,
            'handle': None,
            'subscribers': 0,
            'description': None,
            'profile_pic_url': None,
            'banner_url': None,
            'is_verified': False,
            'video_count': 0,
            'joined_date': None,
            'total_views': 0,
            'country': None,
        }

        try:
            # Wait for page to fully load
            await asyncio.sleep(3)

            # Wait for the channel header to appear
            try:
                await self.page.wait_for_selector('ytd-c4-tabbed-header-renderer, #channel-header', timeout=10000)
            except:
                logger.warning("Channel header did not load")

            # Extract channel name - try multiple selectors (updated for 2024/2025 YouTube)
            name_selectors = [
                'ytd-channel-name yt-formatted-string.ytd-channel-name',
                '#channel-name yt-formatted-string.ytd-channel-name',
                'ytd-c4-tabbed-header-renderer #channel-name yt-formatted-string',
                '#channel-header-container #channel-name',
                'yt-dynamic-text-view-model span.yt-core-attributed-string',
                '#page-header h1 span',
                'h1#channel-header-container',
            ]
            for selector in name_selectors:
                try:
                    element = await self.page.query_selector(selector)
                    if element:
                        info['channel_name'] = await element.text_content()
                        if info['channel_name']:
                            info['channel_name'] = info['channel_name'].strip()
                            if info['channel_name']:
                                logger.debug(f"Found channel name: {info['channel_name']}")
                                break
                except:
                    continue

            # Extract handle (@username) - updated selectors
            handle_selectors = [
                '#channel-handle',
                'yt-formatted-string#channel-handle',
                'ytd-c4-tabbed-header-renderer #channel-handle',
                '#page-header yt-content-metadata-view-model span:first-child',
                '[itemprop="identifier"]',
            ]
            for selector in handle_selectors:
                try:
                    element = await self.page.query_selector(selector)
                    if element:
                        handle = await element.text_content()
                        if handle:
                            handle = handle.strip()
                            if handle.startswith('@'):
                                info['handle'] = handle
                                logger.debug(f"Found handle: {info['handle']}")
                                break
                except:
                    continue

            # Try to get handle from URL if not found
            if not info['handle']:
                current_url = self.page.url
                if '/@' in current_url:
                    handle = '@' + current_url.split('/@')[1].split('/')[0].split('?')[0]
                    info['handle'] = handle
                    logger.debug(f"Extracted handle from URL: {handle}")

            # Extract subscriber count - updated selectors
            subscriber_selectors = [
                '#subscriber-count',
                'yt-formatted-string#subscriber-count',
                'ytd-c4-tabbed-header-renderer #subscriber-count',
                '#page-header yt-content-metadata-view-model span:nth-child(1)',
                'yt-formatted-string.ytd-c4-tabbed-header-renderer:has-text("subscriber")',
            ]
            for selector in subscriber_selectors:
                try:
                    element = await self.page.query_selector(selector)
                    if element:
                        sub_text = await element.text_content()
                        info['subscribers'] = parse_subscriber_count(sub_text)
                        if info['subscribers'] > 0:
                            logger.debug(f"Found subscribers: {info['subscribers']}")
                            break
                except:
                    continue

            # If still no subscribers, try getting all text and finding subscriber pattern
            if info['subscribers'] == 0:
                try:
                    # Look for any element containing subscriber count pattern
                    page_content = await self.page.content()
                    import re
                    sub_patterns = [
                        r'([\d,.]+[KMB]?)\s*subscribers?',
                        r'([\d,.]+)\s*subscribers?',
                    ]
                    for pattern in sub_patterns:
                        match = re.search(pattern, page_content, re.IGNORECASE)
                        if match:
                            info['subscribers'] = parse_subscriber_count(match.group(1))
                            if info['subscribers'] > 0:
                                logger.debug(f"Found subscribers via regex: {info['subscribers']}")
                                break
                except:
                    pass

            # Check for verification badge - updated selectors
            try:
                verified_selectors = [
                    'ytd-badge-supported-renderer .badge-style-type-verified',
                    '[badge-style="BADGE_STYLE_TYPE_VERIFIED"]',
                    'yt-icon.ytd-badge-supported-renderer',
                    '.badge-style-type-verified-artist',
                ]
                for selector in verified_selectors:
                    verified = await self.page.query_selector(selector)
                    if verified:
                        info['is_verified'] = True
                        logger.debug("Channel is verified")
                        break
            except:
                pass

            # Extract profile picture URL - updated selectors
            try:
                avatar_selectors = [
                    '#avatar img',
                    '#channel-header-container img#img',
                    'ytd-c4-tabbed-header-renderer #avatar img',
                    '#page-header yt-avatar-shape img',
                    'yt-img-shadow#avatar img',
                ]
                for selector in avatar_selectors:
                    avatar = await self.page.query_selector(selector)
                    if avatar:
                        src = await avatar.get_attribute('src')
                        if src and 'yt' in src:
                            info['profile_pic_url'] = src
                            logger.debug(f"Found profile pic: {src[:50]}...")
                            break
            except:
                pass

            # Extract banner URL - updated selectors
            try:
                banner_selectors = [
                    '#banner img',
                    'ytd-c4-tabbed-header-renderer #banner img',
                    '.page-header-banner-image img',
                    'yt-image-banner-view-model img',
                ]
                for selector in banner_selectors:
                    banner = await self.page.query_selector(selector)
                    if banner:
                        src = await banner.get_attribute('src')
                        if src:
                            info['banner_url'] = src
                            logger.debug(f"Found banner: {src[:50]}...")
                            break
            except:
                pass

        except Exception as e:
            logger.warning(f"Error extracting channel info: {e}")

        return info

    async def _extract_about_info(self, channel_url: str) -> Dict:
        """Navigate to About tab and extract additional info"""
        about_info = {
            'description': None,
            'total_views': 0,
            'joined_date': None,
            'country': None,
            'links': [],
        }

        try:
            # Navigate to About page
            about_url = channel_url.rstrip('/') + '/about'
            await self.page.goto(about_url, timeout=30000, wait_until='domcontentloaded')
            await asyncio.sleep(2)

            # Extract description
            try:
                desc_element = await self.page.query_selector('#description-container, ytd-channel-about-metadata-renderer #description')
                if desc_element:
                    about_info['description'] = await desc_element.text_content()
                    if about_info['description']:
                        about_info['description'] = about_info['description'].strip()
            except:
                pass

            # Extract stats from about page
            try:
                stats = await self.page.query_selector_all('#right-column yt-formatted-string, ytd-channel-about-metadata-renderer yt-formatted-string')
                for stat in stats:
                    text = await stat.text_content()
                    if text:
                        text_lower = text.lower()
                        if 'view' in text_lower:
                            # Extract total views
                            about_info['total_views'] = parse_view_count(text)
                        elif 'joined' in text_lower:
                            about_info['joined_date'] = text.replace('Joined', '').replace('joined', '').strip()
            except:
                pass

            # Extract country/location
            try:
                location = await self.page.query_selector('#details-container yt-formatted-string:has-text("India"), #details-container yt-formatted-string:has-text("United")')
                if location:
                    about_info['country'] = await location.text_content()
            except:
                pass

            # Extract links
            try:
                link_elements = await self.page.query_selector_all('#link-list-container a, ytd-channel-about-metadata-renderer a')
                for link in link_elements[:5]:  # Limit to 5 links
                    href = await link.get_attribute('href')
                    if href and not href.startswith('/'):
                        about_info['links'].append(href)
            except:
                pass

        except Exception as e:
            logger.warning(f"Error extracting about info: {e}")

        return about_info

    async def _extract_videos(self, channel_url: str, max_videos: int = 6) -> List[Dict]:
        """Navigate to Videos tab and extract recent videos"""
        videos = []

        try:
            # Navigate to Videos page
            videos_url = channel_url.rstrip('/') + '/videos'
            await self.page.goto(videos_url, timeout=30000, wait_until='domcontentloaded')
            await asyncio.sleep(3)

            # Wait for video grid to load
            try:
                await self.page.wait_for_selector('ytd-rich-item-renderer, ytd-grid-video-renderer', timeout=10000)
            except:
                logger.warning("Video grid did not load")
                return videos

            # Extract video items
            video_elements = []
            video_selectors = [
                'ytd-rich-item-renderer',
                'ytd-grid-video-renderer',
            ]

            for selector in video_selectors:
                video_elements = await self.page.query_selector_all(selector)
                if video_elements:
                    break

            for i, video_element in enumerate(video_elements[:max_videos]):
                try:
                    video_data = {
                        'video_id': None,
                        'title': None,
                        'thumbnail_url': None,
                        'view_count': 0,
                        'upload_date': None,
                        'duration': None,
                    }

                    # Extract video link and ID
                    link = await video_element.query_selector('a#thumbnail, a#video-title-link, a#video-title')
                    if link:
                        href = await link.get_attribute('href')
                        if href and '/watch?v=' in href:
                            video_data['video_id'] = href.split('v=')[1].split('&')[0]

                    # Extract title
                    title_element = await video_element.query_selector('#video-title, #video-title-link')
                    if title_element:
                        video_data['title'] = await title_element.get_attribute('title') or await title_element.text_content()
                        if video_data['title']:
                            video_data['title'] = video_data['title'].strip()

                    # Extract thumbnail
                    thumbnail = await video_element.query_selector('img#img, ytd-thumbnail img')
                    if thumbnail:
                        video_data['thumbnail_url'] = await thumbnail.get_attribute('src')

                    # Extract view count and date from metadata
                    metadata_elements = await video_element.query_selector_all('#metadata-line span, #metadata span')
                    for meta in metadata_elements:
                        meta_text = await meta.text_content()
                        if meta_text:
                            if 'view' in meta_text.lower():
                                video_data['view_count'] = parse_view_count(meta_text)
                            elif 'ago' in meta_text.lower() or 'year' in meta_text.lower() or 'month' in meta_text.lower():
                                video_data['upload_date'] = meta_text.strip()

                    # Extract duration
                    duration_element = await video_element.query_selector('ytd-thumbnail-overlay-time-status-renderer span, #overlays span.ytd-thumbnail-overlay-time-status-renderer')
                    if duration_element:
                        video_data['duration'] = await duration_element.text_content()
                        if video_data['duration']:
                            video_data['duration'] = video_data['duration'].strip()

                    if video_data['video_id'] or video_data['title']:
                        videos.append(video_data)

                except Exception as e:
                    logger.warning(f"Error extracting video {i}: {e}")

        except Exception as e:
            logger.warning(f"Error extracting videos: {e}")

        return videos

    async def scrape_channel(self, channel_identifier: str, category: str = None, location: str = None) -> Dict:
        """
        Scrape a YouTube channel's public data

        Args:
            channel_identifier: Channel URL, handle (@username), or channel ID
            category: Optional category for the influencer
            location: Optional location for the influencer

        Returns:
            Dictionary containing channel data
        """
        logger.info(f"Scraping channel: {channel_identifier}")

        # Normalize channel URL
        if channel_identifier.startswith('@'):
            channel_url = f"https://www.youtube.com/{channel_identifier}"
        elif channel_identifier.startswith('UC') and len(channel_identifier) == 24:
            channel_url = f"https://www.youtube.com/channel/{channel_identifier}"
        elif 'youtube.com' in channel_identifier:
            channel_url = channel_identifier
        else:
            # Try as handle
            channel_url = f"https://www.youtube.com/@{channel_identifier}"

        try:
            # Navigate to channel page
            await self.page.goto(channel_url, timeout=30000, wait_until='domcontentloaded')
            await asyncio.sleep(random.uniform(1.5, 3.0))  # Random delay

            # Simulate human behavior - random mouse movements
            await self.simulate_human_behavior()

            # Accept cookies if dialog appears (do this FIRST)
            try:
                # Try multiple cookie consent selectors
                cookie_selectors = [
                    'button[aria-label*="Accept"]',
                    'button:has-text("Accept all")',
                    'button:has-text("Reject all")',
                    'tp-yt-paper-button:has-text("Accept")',
                    'ytd-button-renderer:has-text("Accept") button',
                    '[aria-label="Accept the use of cookies and other data for the purposes described"]',
                ]
                for selector in cookie_selectors:
                    try:
                        accept_button = await self.page.query_selector(selector)
                        if accept_button:
                            await accept_button.click()
                            logger.info("Accepted cookie consent")
                            await asyncio.sleep(2)
                            break
                    except:
                        continue
            except:
                pass

            # Wait for page to load after consent
            await asyncio.sleep(2)

            # Check if channel exists - look for 404 indicators
            page_content = await self.page.content()
            page_title = await self.page.title()
            current_url = self.page.url

            # Check for actual 404 page (be more specific)
            is_404 = (
                "This page isn't available" in page_content or
                "this channel is not available" in page_content.lower() or
                ('404' in page_title and 'youtube' not in page_title.lower()) or
                '/error' in current_url
            )

            if is_404:
                raise ChannelNotFoundException(f"Channel not found: {channel_identifier}")

            # Extract channel info from main page
            channel_info = await self._extract_channel_info()

            # Extract about info
            about_info = await self._extract_about_info(channel_url)

            # Extract recent videos
            videos = await self._extract_videos(channel_url, self.config.get('max_videos_to_scrape', 6))

            # Build channel data
            channel_id = channel_info.get('handle') or channel_identifier.replace('@', '').replace('/', '_')

            channel_data = {
                'channel_id': channel_id,
                'channel_name': channel_info.get('channel_name'),
                'handle': channel_info.get('handle'),
                'channel_url': channel_url,
                'subscribers': channel_info.get('subscribers', 0),
                'video_count': len(videos),  # Will be updated if we can get actual count
                'total_views': about_info.get('total_views', 0),
                'description': about_info.get('description'),
                'profile_pic_url': channel_info.get('profile_pic_url'),
                'banner_url': channel_info.get('banner_url'),
                'is_verified': channel_info.get('is_verified', False),
                'joined_date': about_info.get('joined_date'),
                'country': about_info.get('country'),
                'external_links': about_info.get('links', []),
                'recent_videos': videos,
                'video_thumbnails': [v.get('thumbnail_url') for v in videos if v.get('thumbnail_url')],
                'video_urls': [f"https://www.youtube.com/watch?v={v['video_id']}" for v in videos if v.get('video_id')],
                'influencer_tier': determine_influencer_tier(channel_info.get('subscribers', 0)),
                'category': category,
                'location': location,
                'scrape_timestamp': datetime.now(timezone.utc).isoformat(),
            }

            # Download images
            if channel_data['profile_pic_url']:
                channel_data['profile_pic_local'] = await self.download_image(
                    channel_data['profile_pic_url'],
                    channel_id,
                    'profile'
                )

            if channel_data['banner_url']:
                channel_data['banner_local'] = await self.download_image(
                    channel_data['banner_url'],
                    channel_id,
                    'banner'
                )

            # Download video thumbnails and add local path to each video
            channel_data['video_thumbnails_local'] = []
            for i, video in enumerate(videos):
                if video.get('thumbnail_url'):
                    local_path = await self.download_image(
                        video['thumbnail_url'],
                        channel_id,
                        'video',
                        video_id=video.get('video_id')  # Use video_id as filename
                    )
                    if local_path:
                        channel_data['video_thumbnails_local'].append(local_path)
                        # Add local thumbnail path to the video object
                        video['thumbnail_local'] = local_path

            logger.info(f"Successfully scraped channel: {channel_data.get('channel_name', channel_id)} ({channel_data['subscribers']:,} subscribers)")
            return channel_data

        except ChannelNotFoundException:
            raise
        except Exception as e:
            logger.error(f"Error scraping channel {channel_identifier}: {e}")
            raise

    async def save_channel_data(self, channel_data: Dict) -> str:
        """Save channel data to JSON file"""
        channel_id = channel_data.get('channel_id') or channel_data.get('handle', 'unknown')
        channel_id = channel_id.replace('@', '').replace('/', '_').replace('\\', '_')

        filename = f"{channel_id}.json"
        filepath = self.output_dir / filename

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(channel_data, f, indent=2, ensure_ascii=False)

        logger.info(f"Saved channel data to {filepath}")
        return str(filepath)

    async def cleanup(self):
        """Close browser and cleanup resources"""
        if self.page:
            await self.page.close()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if hasattr(self, 'playwright') and self.playwright:
            await self.playwright.stop()
        logger.info("Browser cleanup complete")


# Progress tracking functions
def load_queue_file(queue_file: str) -> Dict:
    """Load queue file with checkpoint data"""
    try:
        with open(queue_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load queue file {queue_file}: {e}")
        return {'channels': [], 'completed': [], 'failed': {}, 'current_index': 0}


def save_queue_file(queue_file: str, queue_data: Dict):
    """Save queue file with checkpoint data"""
    try:
        with open(queue_file, 'w', encoding='utf-8') as f:
            json.dump(queue_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Failed to save queue file {queue_file}: {e}")


def get_progress_file(queue_file: str = None) -> Path:
    """Get progress file path based on region"""
    base = Path(__file__).parent
    if queue_file and '_us' in queue_file.lower():
        return base / 'data' / 'scraper_progress_us.json'
    elif queue_file and '_gulf' in queue_file.lower():
        return base / 'data' / 'scraper_progress_gulf.json'
    elif queue_file and '_uk' in queue_file.lower():
        return base / 'data' / 'scraper_progress_uk.json'
    return base / 'data' / 'scraper_progress.json'


def load_progress(queue_file: str = None) -> Dict:
    """Load global progress data"""
    progress_file = get_progress_file(queue_file)
    try:
        if progress_file.exists():
            with open(progress_file, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load progress: {e}")

    return {
        'completed_channels': [],
        'failed_channels': {},
        'session_stats': {'success': 0, 'failed': 0, 'skipped': 0},
        'last_updated': None
    }


def save_progress(progress: Dict, queue_file: str = None):
    """Save global progress data"""
    progress_file = get_progress_file(queue_file)
    progress_file.parent.mkdir(parents=True, exist_ok=True)

    progress['last_updated'] = datetime.now(timezone.utc).isoformat()

    try:
        with open(progress_file, 'w', encoding='utf-8') as f:
            json.dump(progress, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Failed to save progress: {e}")


def is_channel_completed(channel_id: str, progress: Dict, output_dir: Path) -> bool:
    """Check if channel has already been scraped"""
    # Check progress file
    if channel_id in progress.get('completed_channels', []):
        return True

    # Check output directory
    safe_id = channel_id.replace('@', '').replace('/', '_').replace('\\', '_')
    channel_file = output_dir / f"{safe_id}.json"
    return channel_file.exists()


async def process_queue(queue_file: str, headless: bool = True, resume: bool = True):
    """Process a queue file of channels to scrape"""
    queue_data = load_queue_file(queue_file)
    progress = load_progress(queue_file)
    output_dir = get_output_dir(queue_file)

    channels = queue_data.get('channels', [])
    if not channels:
        logger.error("No channels found in queue file")
        return

    logger.info(f"Processing queue with {len(channels)} channels")

    # Initialize scraper
    scraper = YouTubeScraperPlaywright(queue_file=queue_file)
    await scraper.start_browser(headless=headless)

    start_index = queue_data.get('current_index', 0) if resume else 0

    try:
        for i, channel_entry in enumerate(channels[start_index:], start=start_index):
            # Handle both string and dict entries
            if isinstance(channel_entry, str):
                channel_id = channel_entry
                category = queue_data.get('category')
                location = queue_data.get('location')
            else:
                channel_id = channel_entry.get('channel_id') or channel_entry.get('handle')
                category = channel_entry.get('category', queue_data.get('category'))
                location = channel_entry.get('location', queue_data.get('location'))

            # Skip if already completed
            if is_channel_completed(channel_id, progress, output_dir):
                logger.info(f"Skipping already completed channel: {channel_id}")
                progress['session_stats']['skipped'] = progress['session_stats'].get('skipped', 0) + 1
                continue

            try:
                # Scrape channel
                channel_data = await scraper.scrape_channel(channel_id, category, location)

                # Save data
                await scraper.save_channel_data(channel_data)

                # Update progress
                progress['completed_channels'].append(channel_id)
                progress['session_stats']['success'] = progress['session_stats'].get('success', 0) + 1
                queue_data['completed'] = queue_data.get('completed', []) + [channel_id]
                queue_data['current_index'] = i + 1

                # Save checkpoint
                save_progress(progress, queue_file)
                save_queue_file(queue_file, queue_data)

                # Random delay between channels
                delay = random.uniform(2, 5)
                logger.info(f"Waiting {delay:.1f}s before next channel...")
                await asyncio.sleep(delay)

            except ChannelNotFoundException as e:
                logger.warning(f"Channel not found: {channel_id}")
                progress['failed_channels'][channel_id] = {'error': str(e), 'attempts': 1}
                progress['session_stats']['failed'] = progress['session_stats'].get('failed', 0) + 1
                queue_data['failed'] = queue_data.get('failed', {})
                queue_data['failed'][channel_id] = {'error': str(e), 'attempts': 1}

            except RateLimitException as e:
                logger.error(f"Rate limited! Waiting before retry...")
                await asyncio.sleep(60)

            except Exception as e:
                logger.error(f"Error processing {channel_id}: {e}")
                progress['failed_channels'][channel_id] = {'error': str(e), 'attempts': 1}
                progress['session_stats']['failed'] = progress['session_stats'].get('failed', 0) + 1

    finally:
        await scraper.cleanup()
        save_progress(progress, queue_file)
        save_queue_file(queue_file, queue_data)

        # Print summary
        stats = progress.get('session_stats', {})
        logger.info("=" * 50)
        logger.info("SCRAPING SESSION COMPLETE")
        logger.info(f"  Success: {stats.get('success', 0)}")
        logger.info(f"  Failed: {stats.get('failed', 0)}")
        logger.info(f"  Skipped: {stats.get('skipped', 0)}")
        logger.info("=" * 50)


async def scrape_single_channel(channel_identifier: str, category: str = None, location: str = None, headless: bool = True) -> Dict:
    """Convenience function to scrape a single channel"""
    scraper = YouTubeScraperPlaywright()
    await scraper.start_browser(headless=headless)

    try:
        channel_data = await scraper.scrape_channel(channel_identifier, category, location)
        await scraper.save_channel_data(channel_data)
        return channel_data
    finally:
        await scraper.cleanup()


def main():
    """Main entry point for CLI usage"""
    import argparse

    parser = argparse.ArgumentParser(description='YouTube Channel Scraper')
    parser.add_argument('--channel', '-c', help='Single channel to scrape (URL, @handle, or channel ID)')
    parser.add_argument('--queue', '-q', help='Queue file to process')
    parser.add_argument('--category', help='Category for the channel(s)')
    parser.add_argument('--location', help='Location for the channel(s)')
    parser.add_argument('--headless', action='store_true', default=True, help='Run browser in headless mode')
    parser.add_argument('--no-headless', action='store_false', dest='headless', help='Show browser window')
    parser.add_argument('--resume', action='store_true', default=True, help='Resume from last checkpoint')
    parser.add_argument('--no-resume', action='store_false', dest='resume', help='Start from beginning')

    args = parser.parse_args()

    if args.channel:
        # Scrape single channel
        asyncio.run(scrape_single_channel(
            args.channel,
            category=args.category,
            location=args.location,
            headless=args.headless
        ))
    elif args.queue:
        # Process queue file
        asyncio.run(process_queue(
            args.queue,
            headless=args.headless,
            resume=args.resume
        ))
    else:
        parser.print_help()
        print("\nExamples:")
        print("  python youtube_channel_scraper.py --channel @mkbhd")
        print("  python youtube_channel_scraper.py --channel https://www.youtube.com/@TechLinked")
        print("  python youtube_channel_scraper.py --queue data/queue/tech_channels.json")


if __name__ == '__main__':
    main()
