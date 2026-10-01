#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Anti-Detection Module for Browser Automation
Provides comprehensive browser fingerprinting, stealth scripts, and human behavior simulation
to avoid bot detection on YouTube and other platforms.
"""

import random
import asyncio
import json
import logging
from typing import Dict, List, Optional, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)


class BrowserFingerprint:
    """Generates realistic browser fingerprints"""

    # Real user agent strings from browser statistics (2024-2025)
    USER_AGENTS = {
        'chrome_windows': [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        ],
        'chrome_mac': [
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        ],
        'firefox_windows': [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0',
        ],
        'firefox_mac': [
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:122.0) Gecko/20100101 Firefox/122.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 14.1; rv:121.0) Gecko/20100101 Firefox/121.0',
        ],
        'safari_mac': [
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
        ],
        'edge_windows': [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0',
        ],
    }

    # Common screen resolutions weighted by popularity
    SCREEN_RESOLUTIONS = [
        {'width': 1920, 'height': 1080, 'weight': 30},  # Most common
        {'width': 1366, 'height': 768, 'weight': 15},
        {'width': 1536, 'height': 864, 'weight': 12},
        {'width': 1440, 'height': 900, 'weight': 10},
        {'width': 1680, 'height': 1050, 'weight': 8},
        {'width': 2560, 'height': 1440, 'weight': 10},
        {'width': 1280, 'height': 720, 'weight': 5},
        {'width': 1600, 'height': 900, 'weight': 5},
        {'width': 3840, 'height': 2160, 'weight': 5},  # 4K
    ]

    TIMEZONES = {
        'us': ['America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles', 'America/Phoenix'],
        'europe': ['Europe/London', 'Europe/Paris', 'Europe/Berlin', 'Europe/Amsterdam', 'Europe/Rome'],
        'asia': ['Asia/Kolkata', 'Asia/Tokyo', 'Asia/Singapore', 'Asia/Shanghai', 'Asia/Dubai'],
        'oceania': ['Australia/Sydney', 'Australia/Melbourne', 'Pacific/Auckland'],
    }

    LOCALES = ['en-US', 'en-GB', 'en-IN', 'en-AU', 'en-CA', 'en-NZ']

    WEBGL_VENDORS = [
        ('Google Inc. (Intel)', 'ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)'),
        ('Google Inc. (Intel)', 'ANGLE (Intel, Intel(R) Iris Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)'),
        ('Google Inc. (NVIDIA)', 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 Ti Direct3D11 vs_5_0 ps_5_0, D3D11)'),
        ('Google Inc. (NVIDIA)', 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)'),
        ('Google Inc. (AMD)', 'ANGLE (AMD, AMD Radeon RX 580 Direct3D11 vs_5_0 ps_5_0, D3D11)'),
        ('Intel Inc.', 'Intel Iris OpenGL Engine'),
        ('Apple Inc.', 'Apple M1'),
        ('Apple Inc.', 'Apple M2'),
    ]

    @classmethod
    def generate(cls, browser_type: str = None, region: str = None) -> Dict:
        """Generate a complete browser fingerprint"""

        # Select browser type based on weights
        if browser_type is None:
            browser_weights = [
                ('chrome_windows', 45),
                ('chrome_mac', 20),
                ('firefox_windows', 10),
                ('safari_mac', 10),
                ('edge_windows', 10),
                ('firefox_mac', 5),
            ]
            browser_type = cls._weighted_choice(browser_weights)

        user_agent = random.choice(cls.USER_AGENTS.get(browser_type, cls.USER_AGENTS['chrome_windows']))

        # Select viewport based on weights
        viewport = cls._weighted_choice([(r, r['weight']) for r in cls.SCREEN_RESOLUTIONS])
        viewport = {'width': viewport['width'], 'height': viewport['height']}

        # Select timezone based on region
        if region:
            region = region.lower()
            tz_list = cls.TIMEZONES.get(region, cls.TIMEZONES['us'])
        else:
            tz_list = random.choice(list(cls.TIMEZONES.values()))
        timezone = random.choice(tz_list)

        # WebGL info
        webgl_vendor, webgl_renderer = random.choice(cls.WEBGL_VENDORS)

        # Platform based on user agent
        if 'Windows' in user_agent:
            platform = 'Win32'
        elif 'Macintosh' in user_agent or 'Mac OS' in user_agent:
            platform = 'MacIntel'
        else:
            platform = 'Win32'

        return {
            'user_agent': user_agent,
            'viewport': viewport,
            'timezone': timezone,
            'locale': random.choice(cls.LOCALES),
            'color_scheme': random.choice(['light', 'dark']),
            'device_scale_factor': random.choice([1, 1.25, 1.5, 2]),
            'has_touch': False,
            'platform': platform,
            'hardware_concurrency': random.choice([4, 6, 8, 12, 16]),
            'device_memory': random.choice([4, 8, 16, 32]),
            'webgl_vendor': webgl_vendor,
            'webgl_renderer': webgl_renderer,
            'languages': ['en-US', 'en'],
            'do_not_track': random.choice([None, '1']),
            'browser_type': browser_type,
        }

    @staticmethod
    def _weighted_choice(choices):
        """Select item based on weights"""
        total = sum(weight for item, weight in choices)
        r = random.uniform(0, total)
        upto = 0
        for item, weight in choices:
            if upto + weight >= r:
                return item
            upto += weight
        return choices[-1][0]


class AntiDetectionManager:
    """Manages anti-detection settings and behaviors"""

    def __init__(self, config_path: str = None):
        self.config = self._load_config(config_path)
        self.fingerprint = None

    def _load_config(self, config_path: str = None) -> Dict:
        """Load anti-detection configuration"""
        if config_path:
            try:
                with open(config_path, 'r') as f:
                    return json.load(f)
            except:
                pass

        return {
            'rotate_fingerprint_per_session': True,
            'enable_request_blocking': True,
            'enable_human_behavior': True,
            'min_delay_between_requests': 1.5,
            'max_delay_between_requests': 5.0,
            'block_webrtc': True,
            'block_canvas_fingerprinting': True,
        }

    def generate_fingerprint(self, browser_type: str = None, region: str = None) -> Dict:
        """Generate a new browser fingerprint"""
        self.fingerprint = BrowserFingerprint.generate(browser_type, region)
        return self.fingerprint

    def get_chrome_args(self) -> List[str]:
        """Get Chrome launch arguments for anti-detection"""
        args = [
            # Core anti-detection
            '--disable-blink-features=AutomationControlled',

            # Stability and sandboxing
            '--disable-dev-shm-usage',
            '--no-sandbox',
            '--disable-setuid-sandbox',

            # Disable automation indicators
            '--disable-infobars',
            '--disable-extensions',
            '--disable-plugins-discovery',
            '--disable-default-apps',
            '--disable-component-extensions-with-background-pages',

            # Performance settings that also help avoid detection
            '--disable-background-timer-throttling',
            '--disable-backgrounding-occluded-windows',
            '--disable-renderer-backgrounding',
            '--disable-features=IsolateOrigins,site-per-process',
            '--disable-ipc-flooding-protection',

            # Window settings
            '--window-size=1920,1080',
            '--start-maximized',
            '--no-first-run',
            '--no-default-browser-check',

            # GPU settings (can help with fingerprinting)
            '--disable-gpu',
            '--disable-accelerated-2d-canvas',

            # Security settings (helps in some cases)
            '--disable-web-security',
            '--allow-running-insecure-content',
            '--ignore-certificate-errors',

            # Disable WebRTC leak (optional but recommended)
            '--disable-rtc-smoothness-algorithm',
            '--disable-webrtc-hw-decoding',
            '--disable-webrtc-hw-encoding',
        ]

        return args

    def get_blocked_resources(self) -> List[str]:
        """Get list of resources to block (fingerprinting/tracking scripts)"""
        return [
            'datadome.co',
            'fingerprintjs',
            'fpjs.io',
            'botd.min.js',
            'sentry.io',
            'segment.io',
            'amplitude.com',
            'mixpanel.com',
            'perimeterx',
            'px-cdn',
            'kasada',
            'distilnetworks',
            'akamaihd.net/*/tpm/',
            'imperva',
            'cloudflare.com/cdn-cgi/bm/',
            'creepjs',
            'recaptcha',  # Be careful - may break some sites
            'hcaptcha',
            'arkoselabs',
            'funcaptcha',
        ]


def get_stealth_scripts(fingerprint: Dict = None) -> str:
    """Generate comprehensive stealth JavaScript injection script"""

    fp = fingerprint or {}
    platform = fp.get('platform', 'Win32')
    hardware_concurrency = fp.get('hardware_concurrency', 8)
    device_memory = fp.get('device_memory', 8)
    webgl_vendor = fp.get('webgl_vendor', 'Google Inc. (Intel)')
    webgl_renderer = fp.get('webgl_renderer', 'ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)')
    languages = fp.get('languages', ['en-US', 'en'])

    return f'''
        // ============================================
        // COMPREHENSIVE ANTI-DETECTION STEALTH SCRIPTS
        // ============================================

        (function() {{
            'use strict';

            // ============ WEBDRIVER DETECTION ============
            // Remove webdriver property
            Object.defineProperty(navigator, 'webdriver', {{
                get: () => undefined,
                configurable: true
            }});

            // Delete from prototype
            try {{
                delete navigator.__proto__.webdriver;
            }} catch(e) {{}}

            // ============ CHROME RUNTIME ============
            window.chrome = {{
                runtime: {{
                    id: undefined,
                    connect: function() {{ return {{}} }},
                    sendMessage: function() {{}},
                    onMessage: {{ addListener: function() {{}}, removeListener: function() {{}} }},
                    onConnect: {{ addListener: function() {{}}, removeListener: function() {{}} }},
                    PlatformOs: {{ MAC: 'mac', WIN: 'win', ANDROID: 'android', CROS: 'cros', LINUX: 'linux', OPENBSD: 'openbsd' }},
                    PlatformArch: {{ ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64' }},
                    PlatformNaclArch: {{ ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64' }},
                    RequestUpdateCheckStatus: {{ THROTTLED: 'throttled', NO_UPDATE: 'no_update', UPDATE_AVAILABLE: 'update_available' }},
                }},
                loadTimes: function() {{
                    return {{
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
                    }};
                }},
                csi: function() {{
                    return {{
                        onloadT: Date.now(),
                        pageT: Math.random() * 1000 + 500,
                        startE: Date.now() - Math.random() * 10000,
                        tran: 15
                    }};
                }},
                app: {{
                    isInstalled: false,
                    InstallState: {{ DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }},
                    RunningState: {{ CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' }},
                }},
            }};

            // ============ PERMISSIONS API ============
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = function(parameters) {{
                if (parameters.name === 'notifications') {{
                    return Promise.resolve({{ state: Notification.permission }});
                }}
                if (parameters.name === 'midi' || parameters.name === 'camera' || parameters.name === 'microphone') {{
                    return Promise.resolve({{ state: 'prompt' }});
                }}
                return originalQuery.call(navigator.permissions, parameters);
            }};

            // ============ PLUGINS ============
            Object.defineProperty(navigator, 'plugins', {{
                get: () => {{
                    const makePlugin = (name, filename, description, mimeTypes) => {{
                        const plugin = {{}};
                        mimeTypes.forEach((mt, i) => {{
                            plugin[i] = {{ type: mt.type, suffixes: mt.suffixes, description: mt.description, enabledPlugin: plugin }};
                        }});
                        plugin.name = name;
                        plugin.filename = filename;
                        plugin.description = description;
                        plugin.length = mimeTypes.length;
                        plugin.item = (i) => plugin[i];
                        plugin.namedItem = (name) => null;
                        return plugin;
                    }};

                    const pluginArray = [
                        makePlugin('Chrome PDF Plugin', 'internal-pdf-viewer', 'Portable Document Format',
                            [{{ type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: 'Portable Document Format' }}]),
                        makePlugin('Chrome PDF Viewer', 'mhjfbmdgcfjbbpaeojofohoefgiehjai', '',
                            [{{ type: 'application/pdf', suffixes: 'pdf', description: '' }}]),
                        makePlugin('Native Client', 'internal-nacl-plugin', '', [
                            {{ type: 'application/x-nacl', suffixes: '', description: 'Native Client Executable' }},
                            {{ type: 'application/x-pnacl', suffixes: '', description: 'Portable Native Client Executable' }}
                        ]),
                    ];
                    pluginArray.item = (i) => pluginArray[i];
                    pluginArray.namedItem = (name) => pluginArray.find(p => p.name === name);
                    pluginArray.refresh = () => {{}};
                    pluginArray.length = 3;
                    return pluginArray;
                }},
            }});

            // ============ MIME TYPES ============
            Object.defineProperty(navigator, 'mimeTypes', {{
                get: () => {{
                    const mimeTypes = [
                        {{ type: 'application/pdf', suffixes: 'pdf', description: '', enabledPlugin: navigator.plugins[1] }},
                        {{ type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: 'Portable Document Format', enabledPlugin: navigator.plugins[0] }},
                        {{ type: 'application/x-nacl', suffixes: '', description: 'Native Client Executable', enabledPlugin: navigator.plugins[2] }},
                        {{ type: 'application/x-pnacl', suffixes: '', description: 'Portable Native Client Executable', enabledPlugin: navigator.plugins[2] }},
                    ];
                    mimeTypes.item = (i) => mimeTypes[i];
                    mimeTypes.namedItem = (name) => mimeTypes.find(m => m.type === name);
                    mimeTypes.length = 4;
                    return mimeTypes;
                }},
            }});

            // ============ LANGUAGES ============
            Object.defineProperty(navigator, 'languages', {{
                get: () => {json.dumps(languages)},
            }});

            Object.defineProperty(navigator, 'language', {{
                get: () => '{languages[0] if languages else "en-US"}',
            }});

            // ============ PLATFORM ============
            Object.defineProperty(navigator, 'platform', {{
                get: () => '{platform}',
            }});

            // ============ HARDWARE ============
            Object.defineProperty(navigator, 'hardwareConcurrency', {{
                get: () => {hardware_concurrency},
            }});

            Object.defineProperty(navigator, 'deviceMemory', {{
                get: () => {device_memory},
            }});

            Object.defineProperty(navigator, 'maxTouchPoints', {{
                get: () => 0,
            }});

            // ============ VENDOR / PRODUCT ============
            Object.defineProperty(navigator, 'vendor', {{
                get: () => 'Google Inc.',
            }});

            Object.defineProperty(navigator, 'productSub', {{
                get: () => '20030107',
            }});

            // ============ CONNECTION ============
            if (navigator.connection) {{
                Object.defineProperty(navigator.connection, 'effectiveType', {{ get: () => '4g' }});
                Object.defineProperty(navigator.connection, 'downlink', {{ get: () => 10 + Math.random() * 5 }});
                Object.defineProperty(navigator.connection, 'rtt', {{ get: () => 50 + Math.floor(Math.random() * 50) }});
                Object.defineProperty(navigator.connection, 'saveData', {{ get: () => false }});
            }}

            // ============ WEBGL ============
            const getParameterHandler = {{
                apply: function(target, thisArg, args) {{
                    const param = args[0];
                    // UNMASKED_VENDOR_WEBGL
                    if (param === 37445) return '{webgl_vendor}';
                    // UNMASKED_RENDERER_WEBGL
                    if (param === 37446) return '{webgl_renderer}';
                    return Reflect.apply(target, thisArg, args);
                }}
            }};

            try {{
                const canvas = document.createElement('canvas');
                ['webgl', 'experimental-webgl', 'webgl2'].forEach(ctxName => {{
                    try {{
                        const ctx = canvas.getContext(ctxName);
                        if (ctx) {{
                            const original = ctx.getParameter;
                            ctx.getParameter = new Proxy(original.bind(ctx), getParameterHandler);
                        }}
                    }} catch(e) {{}}
                }});
            }} catch(e) {{}}

            // ============ CANVAS FINGERPRINT NOISE ============
            const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
            const originalGetImageData = CanvasRenderingContext2D.prototype.getImageData;

            HTMLCanvasElement.prototype.toDataURL = function(type) {{
                if (type === 'image/png' && this.width > 16 && this.height > 16) {{
                    try {{
                        const context = this.getContext('2d');
                        if (context) {{
                            const imageData = originalGetImageData.call(context, 0, 0, Math.min(this.width, 50), Math.min(this.height, 50));
                            const noise = Math.floor(Math.random() * 10) - 5;
                            for (let i = 0; i < imageData.data.length; i += 4) {{
                                if (Math.random() < 0.001) {{
                                    imageData.data[i] = Math.max(0, Math.min(255, imageData.data[i] + noise));
                                }}
                            }}
                            context.putImageData(imageData, 0, 0);
                        }}
                    }} catch(e) {{}}
                }}
                return originalToDataURL.apply(this, arguments);
            }};

            // ============ AUDIO CONTEXT FINGERPRINT ============
            if (window.AudioContext || window.webkitAudioContext) {{
                const AudioCtx = window.AudioContext || window.webkitAudioContext;
                const origCreateAnalyser = AudioCtx.prototype.createAnalyser;
                AudioCtx.prototype.createAnalyser = function() {{
                    const analyser = origCreateAnalyser.apply(this, arguments);
                    const origGetFloatFrequencyData = analyser.getFloatFrequencyData;
                    analyser.getFloatFrequencyData = function(array) {{
                        const result = origGetFloatFrequencyData.apply(this, arguments);
                        for (let i = 0; i < array.length; i++) {{
                            array[i] += Math.random() * 0.0001;
                        }}
                        return result;
                    }};
                    return analyser;
                }};
            }}

            // ============ MEDIA DEVICES ============
            if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {{
                const origEnumerate = navigator.mediaDevices.enumerateDevices.bind(navigator.mediaDevices);
                navigator.mediaDevices.enumerateDevices = function() {{
                    return origEnumerate().then(devices => {{
                        return [
                            {{ deviceId: 'default', groupId: 'default', kind: 'audioinput', label: '' }},
                            {{ deviceId: 'default', groupId: 'default', kind: 'audiooutput', label: '' }},
                            {{ deviceId: 'default', groupId: 'default', kind: 'videoinput', label: '' }},
                        ];
                    }});
                }};
            }}

            // ============ NOTIFICATION ============
            if (!window.Notification) {{
                window.Notification = {{
                    permission: 'default',
                    requestPermission: () => Promise.resolve('default'),
                }};
            }}

            // ============ IFRAME PROTECTION ============
            const origHTMLIframeContentWindow = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow');
            Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {{
                get: function() {{
                    const contentWindow = origHTMLIframeContentWindow.get.call(this);
                    if (contentWindow) {{
                        try {{
                            contentWindow.chrome = window.chrome;
                        }} catch(e) {{}}
                    }}
                    return contentWindow;
                }}
            }});

            // ============ CONSOLE PROTECTION ============
            // Prevent detection via console logs
            const consoleLog = console.log;
            console.log = function() {{
                // Filter out Puppeteer/Playwright detection
                const args = Array.from(arguments);
                const filtered = args.filter(arg => {{
                    if (typeof arg === 'string') {{
                        return !arg.includes('puppeteer') && !arg.includes('playwright');
                    }}
                    return true;
                }});
                if (filtered.length > 0) {{
                    consoleLog.apply(console, filtered);
                }}
            }};

            // ============ ERROR SUPPRESSION ============
            // Don't expose automation errors
            window.addEventListener('error', function(e) {{
                if (e.message && (e.message.includes('webdriver') || e.message.includes('automation'))) {{
                    e.stopImmediatePropagation();
                    return true;
                }}
            }}, true);

            console.log('[Stealth] Anti-detection loaded');

        }})();
    '''


class HumanBehavior:
    """Simulates human-like browser behavior"""

    def __init__(self, page):
        self.page = page

    async def random_mouse_movement(self, count: int = None):
        """Perform random mouse movements"""
        count = count or random.randint(3, 7)
        try:
            viewport = await self.page.evaluate('({ width: window.innerWidth, height: window.innerHeight })')

            for _ in range(count):
                # Generate natural-looking bezier curve movement
                x = random.randint(100, viewport['width'] - 100)
                y = random.randint(100, viewport['height'] - 100)

                # Move in steps for more natural movement
                current_pos = await self.page.evaluate('({ x: 0, y: 0 })')
                steps = random.randint(5, 15)

                for step in range(steps):
                    progress = step / steps
                    # Easing function for natural acceleration/deceleration
                    eased = self._ease_in_out_quad(progress)
                    intermediate_x = int(current_pos['x'] + (x - current_pos['x']) * eased)
                    intermediate_y = int(current_pos['y'] + (y - current_pos['y']) * eased)
                    await self.page.mouse.move(intermediate_x, intermediate_y)
                    await asyncio.sleep(random.uniform(0.01, 0.03))

                await asyncio.sleep(random.uniform(0.1, 0.4))

        except Exception as e:
            logger.debug(f"Mouse movement error: {e}")

    def _ease_in_out_quad(self, t: float) -> float:
        """Quadratic easing for natural movement"""
        return 2 * t * t if t < 0.5 else 1 - pow(-2 * t + 2, 2) / 2

    async def random_scroll(self, direction: str = 'down'):
        """Perform random scrolling behavior"""
        try:
            # Variable scroll amounts
            scroll_amount = random.randint(100, 600)
            if direction == 'up':
                scroll_amount = -scroll_amount

            # Scroll in chunks for realism
            chunks = random.randint(2, 5)
            chunk_amount = scroll_amount // chunks

            for _ in range(chunks):
                await self.page.evaluate(f'window.scrollBy(0, {chunk_amount})')
                await asyncio.sleep(random.uniform(0.05, 0.15))

            # Occasional micro-adjustment
            if random.random() < 0.3:
                adjustment = random.randint(-50, 50)
                await self.page.evaluate(f'window.scrollBy(0, {adjustment})')

        except Exception as e:
            logger.debug(f"Scroll error: {e}")

    async def random_pause(self, min_seconds: float = 0.5, max_seconds: float = 3.0):
        """Random pause simulating reading/thinking"""
        pause_time = random.uniform(min_seconds, max_seconds)
        await asyncio.sleep(pause_time)

    async def simulate_reading(self, element_selector: str = None):
        """Simulate reading behavior on page"""
        try:
            # Focus on content area
            if element_selector:
                element = await self.page.query_selector(element_selector)
                if element:
                    await element.scroll_into_view_if_needed()

            # Read pattern: scroll down slowly, pause, sometimes scroll back
            read_time = random.uniform(2, 8)
            start_time = asyncio.get_event_loop().time()

            while asyncio.get_event_loop().time() - start_time < read_time:
                # Small scroll
                await self.page.evaluate(f'window.scrollBy(0, {random.randint(20, 80)})')
                await asyncio.sleep(random.uniform(0.3, 1.0))

                # Occasional pause (reading a section)
                if random.random() < 0.2:
                    await asyncio.sleep(random.uniform(0.5, 2.0))

                # Occasional scroll back up (re-reading)
                if random.random() < 0.1:
                    await self.page.evaluate(f'window.scrollBy(0, -{random.randint(30, 100)})')
                    await asyncio.sleep(random.uniform(0.2, 0.5))

        except Exception as e:
            logger.debug(f"Reading simulation error: {e}")

    async def random_click_delay(self) -> float:
        """Get random delay before clicking (human reaction time)"""
        # Human reaction time is typically 150-300ms
        return random.uniform(0.15, 0.4)

    async def hover_before_click(self, selector: str):
        """Hover over element before clicking (human behavior)"""
        try:
            element = await self.page.query_selector(selector)
            if element:
                box = await element.bounding_box()
                if box:
                    # Move to element with some randomness
                    x = box['x'] + box['width'] * random.uniform(0.3, 0.7)
                    y = box['y'] + box['height'] * random.uniform(0.3, 0.7)
                    await self.page.mouse.move(x, y)
                    await asyncio.sleep(random.uniform(0.1, 0.3))
                    await element.click()
        except Exception as e:
            logger.debug(f"Hover click error: {e}")


# Convenience functions
def create_stealth_context_options(fingerprint: Dict = None) -> Dict:
    """Create context options for Playwright with anti-detection settings"""
    fp = fingerprint or BrowserFingerprint.generate()

    return {
        'viewport': fp['viewport'],
        'user_agent': fp['user_agent'],
        'locale': fp['locale'],
        'timezone_id': fp['timezone'],
        'color_scheme': fp.get('color_scheme', 'light'),
        'device_scale_factor': fp.get('device_scale_factor', 1),
        'has_touch': fp.get('has_touch', False),
        'java_script_enabled': True,
        'bypass_csp': True,
    }


if __name__ == '__main__':
    # Test fingerprint generation
    print("Testing Anti-Detection Module...")

    manager = AntiDetectionManager()
    fp = manager.generate_fingerprint()

    print(f"\nGenerated Fingerprint:")
    print(f"  User Agent: {fp['user_agent'][:60]}...")
    print(f"  Viewport: {fp['viewport']}")
    print(f"  Timezone: {fp['timezone']}")
    print(f"  Locale: {fp['locale']}")
    print(f"  Platform: {fp['platform']}")
    print(f"  Hardware Concurrency: {fp['hardware_concurrency']}")
    print(f"  WebGL: {fp['webgl_vendor']}")

    print(f"\nChrome Args: {len(manager.get_chrome_args())} arguments")
    print(f"Blocked Resources: {len(manager.get_blocked_resources())} patterns")

    print("\n✅ Anti-detection module ready!")
