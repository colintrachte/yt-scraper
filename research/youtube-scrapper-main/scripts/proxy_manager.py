#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Residential Proxy Manager for YouTube Channel Scraper
Supports Bright Data, IProyal, Storm Proxies, NetNut, and custom providers.
Provides Playwright-compatible proxy config, session rotation, and env/config loading.
"""

import json
import os
import logging
import random
import string
from typing import Dict, Optional
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Default host/port per provider
PROVIDER_DEFAULTS = {
    "brightdata": {
        "host": "brd.superproxy.io",
        "port": 22225,
    },
    "iproyal": {
        "host": "proxy.iproyal.com",
        "port": 12321,
    },
    "stormproxies": {
        "host": "rotating.stormproxies.com",
        "port": 9999,
    },
    "netnut": {
        "host": "gw-resi.netnut.io",
        "port": 5959,
    },
}


class ProxyManager:
    """
    Manages residential proxy connections for Playwright and requests/aiohttp.

    Usage:
        # From config file
        pm = ProxyManager.from_config("resources/scraper_config_ind.json")

        # From environment variables
        pm = ProxyManager.from_env()

        # Manual
        pm = ProxyManager(provider="brightdata", username="u", password="p", country="us")

        # Playwright integration
        proxy = pm.get_playwright_proxy()
        context = await browser.new_context(proxy=proxy, ...)

        # Rotate IP
        pm.rotate_session()
    """

    def __init__(
        self,
        provider: str = "brightdata",
        host: Optional[str] = None,
        port: Optional[int] = None,
        username: str = "",
        password: str = "",
        country: str = "",
        sticky: bool = True,
        sticky_ttl_minutes: int = 10,
        enabled: bool = True,
    ):
        self.provider = provider.lower() if provider else "brightdata"
        self.enabled = enabled
        self.username = username
        self.password = password
        self.country = country
        self.sticky = sticky
        self.sticky_ttl_minutes = sticky_ttl_minutes

        # Resolve host/port from provider defaults or explicit values
        defaults = PROVIDER_DEFAULTS.get(self.provider, {})
        self.host = host or defaults.get("host", "")
        self.port = port or defaults.get("port", 0)

        # Session ID for sticky sessions
        self._session_id = self._generate_session_id()

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, config_path: Optional[str] = None) -> "ProxyManager":
        """
        Build a ProxyManager from a JSON config file.

        Looks for a top-level ``"proxy"`` key in the config.  If the key is
        missing or ``enabled`` is false, returns a disabled ProxyManager.
        """
        if config_path is None:
            # Try to find any config in resources/
            resources = Path(__file__).parent.parent / "resources"
            candidates = sorted(resources.glob("scraper_config_*.json"))
            if candidates:
                config_path = str(candidates[0])
            else:
                logger.warning("No config file found – returning disabled ProxyManager")
                return cls(enabled=False)

        try:
            with open(config_path, "r") as f:
                config = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load config {config_path}: {e}")
            return cls(enabled=False)

        proxy_cfg = config.get("proxy", {})
        if not proxy_cfg or not proxy_cfg.get("enabled", False):
            return cls(enabled=False)

        return cls(
            provider=proxy_cfg.get("provider", "brightdata"),
            host=proxy_cfg.get("host"),
            port=proxy_cfg.get("port"),
            username=proxy_cfg.get("username", ""),
            password=proxy_cfg.get("password", ""),
            country=proxy_cfg.get("country", ""),
            sticky=proxy_cfg.get("sticky", True),
            sticky_ttl_minutes=proxy_cfg.get("sticky_ttl_minutes", 10),
            enabled=True,
        )

    @classmethod
    def from_env(cls) -> "ProxyManager":
        """
        Build a ProxyManager from environment variables.

        Reads:
            PROXY_ENABLED, PROXY_PROVIDER, PROXY_HOST, PROXY_PORT,
            PROXY_USERNAME, PROXY_PASSWORD, PROXY_COUNTRY, PROXY_STICKY
        """
        enabled = os.getenv("PROXY_ENABLED", "false").lower() in ("true", "1", "yes")
        if not enabled:
            return cls(enabled=False)

        return cls(
            provider=os.getenv("PROXY_PROVIDER", "brightdata"),
            host=os.getenv("PROXY_HOST"),
            port=int(os.getenv("PROXY_PORT", 0)) or None,
            username=os.getenv("PROXY_USERNAME", ""),
            password=os.getenv("PROXY_PASSWORD", ""),
            country=os.getenv("PROXY_COUNTRY", ""),
            sticky=os.getenv("PROXY_STICKY", "true").lower() in ("true", "1", "yes"),
            enabled=True,
        )

    # ------------------------------------------------------------------
    # Proxy URL builders
    # ------------------------------------------------------------------

    def _build_username(self) -> str:
        """
        Build the proxy username string with optional country and session tags.

        Provider-specific formatting:
            brightdata : brd-customer-<user>-zone-residential-country-<cc>-session-<sid>
            iproyal    : <user>-country-<cc>-session-<sid>
            stormproxies / netnut / custom : <user> (plain)
        """
        user = self.username

        if self.provider == "brightdata":
            parts = [f"brd-customer-{user}", "zone-residential"]
            if self.country:
                parts.append(f"country-{self.country}")
            if self.sticky:
                parts.append(f"session-{self._session_id}")
            return "-".join(parts)

        if self.provider == "iproyal":
            parts = [user]
            if self.country:
                parts.append(f"country-{self.country}")
            if self.sticky:
                parts.append(f"session-{self._session_id}")
            return "_".join(parts)  # IProyal uses underscore

        # For stormproxies, netnut, custom — just return the raw user
        return user

    def get_playwright_proxy(self) -> Optional[Dict[str, str]]:
        """
        Return a dict suitable for ``browser.new_context(proxy=...)``.

        Returns ``None`` when the proxy is disabled.
        """
        if not self.enabled:
            return None

        if not self.host or not self.port:
            logger.warning("Proxy enabled but host/port not set – skipping proxy")
            return None

        server = f"http://{self.host}:{self.port}"
        proxy_dict = {"server": server}

        if self.username:
            proxy_dict["username"] = self._build_username()
        if self.password:
            proxy_dict["password"] = self.password

        return proxy_dict

    def get_requests_proxy(self) -> Optional[Dict[str, str]]:
        """
        Return a dict suitable for ``requests.get(proxies=...)`` or ``aiohttp``.

        Returns ``None`` when the proxy is disabled.
        """
        if not self.enabled:
            return None

        if not self.host or not self.port:
            return None

        user_part = self._build_username()
        if user_part and self.password:
            auth = f"{user_part}:{self.password}@"
        elif user_part:
            auth = f"{user_part}@"
        else:
            auth = ""

        url = f"http://{auth}{self.host}:{self.port}"
        return {"http": url, "https": url}

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_session_id(length: int = 8) -> str:
        """Generate a random alphanumeric session ID."""
        return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))

    def rotate_session(self) -> str:
        """
        Generate a new session ID, effectively rotating the IP for sticky
        session providers.  Returns the new session ID.
        """
        old = self._session_id
        self._session_id = self._generate_session_id()
        logger.info(f"Proxy session rotated: {old} → {self._session_id}")
        return self._session_id

    # ------------------------------------------------------------------
    # Info / repr
    # ------------------------------------------------------------------

    def info(self) -> Dict:
        """Return a JSON-serializable summary of the proxy configuration."""
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "host": self.host,
            "port": self.port,
            "country": self.country,
            "sticky": self.sticky,
            "sticky_ttl_minutes": self.sticky_ttl_minutes,
            "session_id": self._session_id,
            "has_credentials": bool(self.username and self.password),
        }

    def __repr__(self) -> str:
        state = "enabled" if self.enabled else "disabled"
        return (
            f"<ProxyManager provider={self.provider} {state} "
            f"host={self.host}:{self.port}>"
        )

    def __bool__(self) -> bool:
        """Truthy when the proxy is enabled and has a valid host."""
        return self.enabled and bool(self.host) and bool(self.port)
