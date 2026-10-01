#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube Channel Discovery and Scraping Orchestrator
Resilient pipeline that handles discovery -> scraping with automatic retries and failure recovery
"""

import asyncio
import json
import os
import sys
import logging
import time
import traceback
from typing import List, Dict, Optional
from pathlib import Path
from datetime import datetime, timezone
import argparse
import signal
import random

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

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('orchestrator.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)


class GracefulExit(Exception):
    """Exception raised for graceful shutdown"""
    pass


class OrchestrationState:
    """Manages the state of the orchestration pipeline"""
    
    def __init__(self, state_file: str = None, region: str = None):
        self.region = region or 'default'
        self.state_dir = Path(__file__).parent / 'data' / 'orchestration'
        self.state_dir.mkdir(parents=True, exist_ok=True)
        
        if state_file:
            self.state_file = Path(state_file)
        else:
            self.state_file = self.state_dir / f"orchestration_state_{self.region}.json"
        
        self.state = self._load_state()
    
    def _load_state(self) -> Dict:
        """Load state from file"""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    state = json.load(f)
                    logger.info(f"Loaded orchestration state from {self.state_file}")
                    return state
            except Exception as e:
                logger.error(f"Failed to load state: {e}")
        
        return self._create_initial_state()
    
    def _create_initial_state(self) -> Dict:
        """Create initial state structure"""
        return {
            'region': self.region,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'last_updated': datetime.now(timezone.utc).isoformat(),
            'phase': 'discovery',  # discovery, scraping, completed
            'discovery': {
                'status': 'pending',  # pending, running, completed, failed
                'progress_file': None,
                'queue_files': [],
                'attempts': 0,
                'last_error': None,
                'started_at': None,
                'completed_at': None
            },
            'scraping': {
                'status': 'pending',
                'current_queue_index': 0,
                'queue_files': [],
                'completed_queues': [],
                'failed_queues': {},
                'channels_scraped': 0,
                'channels_failed': 0,
                'attempts': 0,
                'last_error': None,
                'started_at': None,
                'completed_at': None
            },
            'stats': {
                'total_channels_discovered': 0,
                'total_channels_scraped': 0,
                'total_channels_failed': 0,
                'discovery_attempts': 0,
                'scraping_attempts': 0,
                'browser_restarts': 0
            },
            'config': {
                'max_discovery_retries': 5,
                'max_scrape_retries': 3,
                'max_channel_retries': 3,
                'browser_restart_interval': 50,  # Restart browser every N channels
                'cooldown_after_failure': 60,  # seconds
                'cooldown_multiplier': 2,  # exponential backoff
                'max_cooldown': 600  # max 10 minutes
            }
        }
    
    def save(self):
        """Save state to file"""
        self.state['last_updated'] = datetime.now(timezone.utc).isoformat()
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(self.state, f, indent=2, ensure_ascii=False)
            logger.debug(f"State saved to {self.state_file}")
        except Exception as e:
            logger.error(f"Failed to save state: {e}")
    
    def update_phase(self, phase: str):
        """Update current phase"""
        self.state['phase'] = phase
        self.save()
    
    def update_discovery_status(self, status: str, **kwargs):
        """Update discovery status"""
        self.state['discovery']['status'] = status
        for key, value in kwargs.items():
            if key in self.state['discovery']:
                self.state['discovery'][key] = value
        self.save()
    
    def update_scraping_status(self, status: str, **kwargs):
        """Update scraping status"""
        self.state['scraping']['status'] = status
        for key, value in kwargs.items():
            if key in self.state['scraping']:
                self.state['scraping'][key] = value
        self.save()
    
    def increment_stat(self, stat_name: str, amount: int = 1):
        """Increment a stat counter"""
        if stat_name in self.state['stats']:
            self.state['stats'][stat_name] += amount
            self.save()


class ResilientOrchestrator:
    """Orchestrates discovery and scraping with resilience"""
    
    def __init__(
        self,
        config_path: str = None,
        region: str = None,
        state_file: str = None,
        headless: bool = True,
        categories: List[str] = None,
        locations: List[str] = None,
        resume: bool = False
    ):
        self.config_path = config_path
        self.region = region or self._detect_region(config_path)
        self.headless = headless
        self.categories = categories
        self.locations = locations
        self.resume = resume
        
        # Load config
        self.config = self._load_config()
        
        # Initialize state manager
        self.state_manager = OrchestrationState(
            state_file=state_file if resume else None, 
            region=self.region
        )
        
        # Shutdown flag
        self.shutdown_requested = False
        
        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        logger.warning(f"Received signal {signum}. Initiating graceful shutdown...")
        self.shutdown_requested = True
    
    def _detect_region(self, config_path: str) -> str:
        """Detect region from config path"""
        if not config_path:
            return 'default'
        
        config_path_lower = config_path.lower()
        for region in ['us', 'uk', 'eur', 'east', 'gulf', 'ind']:
            if f'_{region}' in config_path_lower:
                return region
        return 'default'
    
    def _load_config(self) -> Dict:
        """Load configuration"""
        if self.config_path:
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not load config: {e}")
        
        return {
            'categories': ['tech', 'gaming', 'beauty'],
            'locations': ['India'],
            'max_retries': 3,
            'search_delay': [3, 7],
            'scrape_delay': [2, 5]
        }
    
    def _calculate_cooldown(self, attempts: int) -> int:
        """Calculate cooldown with exponential backoff"""
        config = self.state_manager.state['config']
        cooldown = config['cooldown_after_failure'] * (config['cooldown_multiplier'] ** (attempts - 1))
        return min(cooldown, config['max_cooldown'])
    
    async def run_discovery(self) -> Optional[str]:
        """Run channel discovery with retries"""
        from youtube_channel_discovery import YouTubeChannelDiscovery
        
        state = self.state_manager.state
        max_retries = state['config']['max_discovery_retries']
        discovery_state = state['discovery']
        
        # Check if already completed
        if discovery_state['status'] == 'completed' and discovery_state['queue_files']:
            logger.info("Discovery already completed. Skipping.")
            return discovery_state['queue_files'][0] if discovery_state['queue_files'] else None
        
        # Get or create progress file for resume
        progress_file = discovery_state.get('progress_file')
        should_resume = progress_file is not None and Path(progress_file).exists()
        
        for attempt in range(discovery_state['attempts'] + 1, max_retries + 1):
            if self.shutdown_requested:
                raise GracefulExit("Shutdown requested")
            
            self.state_manager.update_discovery_status(
                'running',
                attempts=attempt,
                started_at=datetime.now(timezone.utc).isoformat()
            )
            self.state_manager.increment_stat('discovery_attempts')
            
            logger.info(f"Starting discovery attempt {attempt}/{max_retries}...")
            
            discovery = None
            try:
                discovery = YouTubeChannelDiscovery(config_path=self.config_path)
                await discovery.start_browser(headless=self.headless)
                
                channels = await discovery.discover_channels(
                    categories=self.categories or self.config.get('categories'),
                    locations=self.locations or self.config.get('locations'),
                    resume=should_resume,
                    progress_file=progress_file
                )
                
                if channels:
                    # Create queue file
                    queue_file = discovery.create_queue_file(
                        channels,
                        category=None,
                        location=None
                    )
                    
                    # Get progress file path
                    session_id = datetime.now().strftime('%Y%m%d_%H%M%S')
                    actual_progress_file = str(discovery.progress_dir / f"discovery_progress_{discovery.region or 'default'}_{session_id}.json")
                    
                    self.state_manager.update_discovery_status(
                        'completed',
                        queue_files=[queue_file],
                        progress_file=actual_progress_file,
                        completed_at=datetime.now(timezone.utc).isoformat()
                    )
                    self.state_manager.state['stats']['total_channels_discovered'] = len(channels)
                    self.state_manager.save()
                    
                    logger.info(f"Discovery completed! Found {len(channels)} channels.")
                    return queue_file
                else:
                    logger.warning("No channels discovered in this attempt.")
                    
            except GracefulExit:
                raise
            except Exception as e:
                logger.error(f"Discovery attempt {attempt} failed: {e}")
                logger.debug(traceback.format_exc())
                self.state_manager.update_discovery_status(
                    'failed',
                    last_error=str(e),
                    attempts=attempt
                )
                
                if attempt < max_retries:
                    cooldown = self._calculate_cooldown(attempt)
                    logger.info(f"Waiting {cooldown}s before retry...")
                    await asyncio.sleep(cooldown)
                    should_resume = True  # Enable resume for next attempt
                    
            finally:
                if discovery:
                    try:
                        await discovery.cleanup()
                    except:
                        pass
        
        logger.error(f"Discovery failed after {max_retries} attempts")
        return None
    
    async def run_scraping(self, queue_file: str) -> Dict:
        """Run channel scraping with retries and browser restart"""
        from youtube_channel_scraper import (
            YouTubeScraperPlaywright, 
            load_queue_file, 
            save_queue_file,
            load_progress,
            save_progress,
            get_output_dir,
            is_channel_completed,
            ChannelNotFoundException,
            ChannelSkippedException,
            RateLimitException
        )
        
        state = self.state_manager.state
        config = state['config']
        scraping_state = state['scraping']
        
        # Add queue file if not already tracked
        if queue_file not in scraping_state['queue_files']:
            scraping_state['queue_files'].append(queue_file)
            self.state_manager.save()
        
        self.state_manager.update_scraping_status(
            'running',
            started_at=datetime.now(timezone.utc).isoformat()
        )
        
        # Load queue data
        queue_data = load_queue_file(queue_file)
        progress = load_progress(queue_file)
        output_dir = get_output_dir(queue_file)
        
        channels = queue_data.get('channels', [])
        if not channels:
            logger.error("No channels in queue file")
            return {'success': 0, 'failed': 0, 'skipped': 0}
        
        total_channels = len(channels)
        start_index = queue_data.get('current_index', 0)
        
        logger.info(f"Starting scraping: {total_channels} channels (from index {start_index})")
        
        scraper = None
        channels_since_restart = 0
        stats = {'success': 0, 'failed': 0, 'skipped': 0}
        
        try:
            scraper = YouTubeScraperPlaywright(config_path=self.config_path, queue_file=queue_file)
            await scraper.start_browser(headless=self.headless)
            
            for i in range(start_index, total_channels):
                if self.shutdown_requested:
                    raise GracefulExit("Shutdown requested")
                
                channel_entry = channels[i]
                
                # Handle both string and dict entries
                if isinstance(channel_entry, str):
                    channel_id = channel_entry
                    category = queue_data.get('category')
                    location = queue_data.get('location')
                else:
                    channel_id = channel_entry.get('channel_id') or channel_entry.get('handle')
                    category = channel_entry.get('category', queue_data.get('category'))
                    location = channel_entry.get('location', queue_data.get('location'))
                
                # Skip completed
                if is_channel_completed(channel_id, progress, output_dir):
                    logger.info(f"[{i+1}/{total_channels}] Skipping completed: {channel_id}")
                    stats['skipped'] += 1
                    continue
                
                # Check retry count
                failed_info = queue_data.get('failed', {}).get(channel_id, {})
                attempts = failed_info.get('attempts', 0)
                
                if attempts >= config['max_channel_retries']:
                    logger.warning(f"[{i+1}/{total_channels}] Skipping {channel_id} (exceeded {config['max_channel_retries']} retries)")
                    stats['failed'] += 1
                    continue
                
                # Browser restart check
                if channels_since_restart >= config['browser_restart_interval']:
                    logger.info("Restarting browser to prevent memory issues...")
                    # Rotate proxy session for a fresh IP
                    if hasattr(scraper, 'proxy_manager') and scraper.proxy_manager and scraper.proxy_manager.enabled:
                        scraper.proxy_manager.rotate_session()
                    await scraper.cleanup()
                    await asyncio.sleep(5)
                    await scraper.start_browser(headless=self.headless)
                    channels_since_restart = 0
                    self.state_manager.increment_stat('browser_restarts')
                
                try:
                    logger.info(f"[{i+1}/{total_channels}] Scraping: {channel_id}")
                    
                    channel_data = await scraper.scrape_channel(channel_id, category, location)
                    await scraper.save_channel_data(channel_data)
                    
                    # Update progress
                    progress['completed_channels'].append(channel_id)
                    queue_data['completed'] = queue_data.get('completed', []) + [channel_id]
                    queue_data['current_index'] = i + 1
                    
                    # Remove from failed if was there
                    if channel_id in queue_data.get('failed', {}):
                        del queue_data['failed'][channel_id]
                    
                    stats['success'] += 1
                    channels_since_restart += 1
                    
                    self.state_manager.state['stats']['total_channels_scraped'] += 1
                    self.state_manager.save()
                    
                    # Save checkpoint
                    save_progress(progress, queue_file)
                    save_queue_file(queue_file, queue_data)
                    
                    # Delay
                    delay_range = self.config.get('scrape_delay', [2, 5])
                    delay = random.uniform(*delay_range)
                    logger.info(f"Waiting {delay:.1f}s before next channel...")
                    await asyncio.sleep(delay)
                    
                except ChannelSkippedException as e:
                    logger.info(f"Channel skipped (already exists): {channel_id}")
                    stats['skipped'] += 1
                    queue_data['current_index'] = i + 1
                    save_queue_file(queue_file, queue_data)
                    
                except ChannelNotFoundException as e:
                    logger.warning(f"Channel not found: {channel_id}")
                    self._record_channel_failure(queue_data, progress, channel_id, str(e))
                    stats['failed'] += 1
                    queue_data['current_index'] = i + 1
                    save_queue_file(queue_file, queue_data)
                    save_progress(progress, queue_file)
                    
                except RateLimitException as e:
                    logger.error(f"Rate limited! Implementing cooldown...")
                    self._record_channel_failure(queue_data, progress, channel_id, str(e))
                    
                    # Rotate proxy session for a fresh IP on rate limit
                    if hasattr(scraper, 'proxy_manager') and scraper.proxy_manager and scraper.proxy_manager.enabled:
                        scraper.proxy_manager.rotate_session()
                    
                    # Restart browser and wait
                    await scraper.cleanup()
                    cooldown = self._calculate_cooldown(attempts + 1)
                    logger.info(f"Waiting {cooldown}s before continuing...")
                    await asyncio.sleep(cooldown)
                    await scraper.start_browser(headless=self.headless)
                    channels_since_restart = 0
                    
                except Exception as e:
                    logger.error(f"Error scraping {channel_id}: {e}")
                    logger.debug(traceback.format_exc())
                    self._record_channel_failure(queue_data, progress, channel_id, str(e))
                    stats['failed'] += 1
                    
                    self.state_manager.state['stats']['total_channels_failed'] += 1
                    self.state_manager.save()
                    
                    queue_data['current_index'] = i + 1
                    save_queue_file(queue_file, queue_data)
                    save_progress(progress, queue_file)
                    
                    # Check if too many consecutive failures
                    if self._check_consecutive_failures(queue_data) >= 5:
                        logger.warning("Too many consecutive failures. Restarting browser...")
                        if hasattr(scraper, 'proxy_manager') and scraper.proxy_manager and scraper.proxy_manager.enabled:
                            scraper.proxy_manager.rotate_session()
                        await scraper.cleanup()
                        await asyncio.sleep(30)
                        await scraper.start_browser(headless=self.headless)
                        channels_since_restart = 0
                        self.state_manager.increment_stat('browser_restarts')
        
        except GracefulExit:
            logger.info("Graceful shutdown in progress...")
            raise
            
        finally:
            if scraper:
                try:
                    await scraper.cleanup()
                except:
                    pass
            
            # Final save
            save_queue_file(queue_file, queue_data)
            save_progress(progress, queue_file)
            
            self.state_manager.update_scraping_status(
                'completed' if not self.shutdown_requested else 'interrupted',
                completed_at=datetime.now(timezone.utc).isoformat(),
                channels_scraped=stats['success'],
                channels_failed=stats['failed']
            )
        
        return stats
    
    def _record_channel_failure(self, queue_data: Dict, progress: Dict, channel_id: str, error: str):
        """Record a channel failure with retry tracking"""
        if 'failed' not in queue_data:
            queue_data['failed'] = {}
        
        if channel_id in queue_data['failed']:
            queue_data['failed'][channel_id]['attempts'] += 1
            queue_data['failed'][channel_id]['last_error'] = error
        else:
            queue_data['failed'][channel_id] = {
                'attempts': 1,
                'last_error': error,
                'first_failed_at': datetime.now(timezone.utc).isoformat()
            }
        
        progress['failed_channels'][channel_id] = queue_data['failed'][channel_id]
    
    def _check_consecutive_failures(self, queue_data: Dict) -> int:
        """Check number of recent consecutive failures"""
        failed = queue_data.get('failed', {})
        completed = queue_data.get('completed', [])
        
        if not failed:
            return 0
        
        # Get channels in order
        channels = queue_data.get('channels', [])
        current_idx = queue_data.get('current_index', 0)
        
        consecutive = 0
        for i in range(current_idx - 1, max(0, current_idx - 10), -1):
            if i >= len(channels):
                continue
            channel_entry = channels[i]
            channel_id = channel_entry if isinstance(channel_entry, str) else channel_entry.get('channel_id')
            
            if channel_id in failed:
                consecutive += 1
            else:
                break
        
        return consecutive
    
    async def run(self) -> Dict:
        """Run the full orchestration pipeline"""
        logger.info("=" * 60)
        logger.info(f"YOUTUBE ORCHESTRATOR - Region: {self.region}")
        logger.info("=" * 60)
        
        state = self.state_manager.state
        results = {
            'discovery': {'status': 'pending', 'channels': 0},
            'scraping': {'status': 'pending', 'success': 0, 'failed': 0, 'skipped': 0}
        }
        
        try:
            # Phase 1: Discovery
            if state['phase'] in ['discovery', 'pending'] or state['discovery']['status'] != 'completed':
                self.state_manager.update_phase('discovery')
                logger.info("\n" + "=" * 40)
                logger.info("PHASE 1: CHANNEL DISCOVERY")
                logger.info("=" * 40)
                
                queue_file = await self.run_discovery()
                
                if queue_file:
                    results['discovery'] = {
                        'status': 'completed',
                        'queue_file': queue_file,
                        'channels': state['stats']['total_channels_discovered']
                    }
                else:
                    results['discovery'] = {'status': 'failed', 'channels': 0}
                    logger.error("Discovery failed. Cannot proceed to scraping.")
                    return results
            else:
                queue_file = state['discovery']['queue_files'][0] if state['discovery']['queue_files'] else None
                results['discovery'] = {
                    'status': 'completed',
                    'queue_file': queue_file,
                    'channels': state['stats']['total_channels_discovered']
                }
                logger.info(f"Using existing queue file: {queue_file}")
            
            # Phase 2: Scraping
            if queue_file and (state['phase'] != 'completed' or state['scraping']['status'] != 'completed'):
                self.state_manager.update_phase('scraping')
                logger.info("\n" + "=" * 40)
                logger.info("PHASE 2: CHANNEL SCRAPING")
                logger.info("=" * 40)
                
                scrape_stats = await self.run_scraping(queue_file)
                results['scraping'] = {
                    'status': 'completed',
                    **scrape_stats
                }
            
            # Mark completed
            self.state_manager.update_phase('completed')
            
        except GracefulExit:
            logger.info("Orchestration interrupted. Progress saved.")
            results['status'] = 'interrupted'
            
        except Exception as e:
            logger.error(f"Orchestration error: {e}")
            logger.debug(traceback.format_exc())
            results['status'] = 'error'
            results['error'] = str(e)
        
        # Print summary
        self._print_summary(results)
        
        return results
    
    def _print_summary(self, results: Dict):
        """Print final summary"""
        logger.info("\n" + "=" * 60)
        logger.info("ORCHESTRATION SUMMARY")
        logger.info("=" * 60)
        
        state = self.state_manager.state
        
        logger.info(f"Region: {self.region}")
        logger.info(f"Discovery Status: {results['discovery'].get('status', 'unknown')}")
        logger.info(f"Channels Discovered: {results['discovery'].get('channels', 0)}")
        logger.info(f"Scraping Status: {results['scraping'].get('status', 'unknown')}")
        logger.info(f"  - Success: {results['scraping'].get('success', 0)}")
        logger.info(f"  - Failed: {results['scraping'].get('failed', 0)}")
        logger.info(f"  - Skipped: {results['scraping'].get('skipped', 0)}")
        logger.info(f"Browser Restarts: {state['stats'].get('browser_restarts', 0)}")
        logger.info(f"State File: {self.state_manager.state_file}")
        logger.info("=" * 60)


async def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='YouTube Channel Discovery and Scraping Orchestrator',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run with India config
  python youtube_orchestrator.py --config config/scraper_config_ind.json

  # Run with US config, show browser
  python youtube_orchestrator.py --config config/scraper_config_us.json --no-headless

  # Resume from existing state
  python youtube_orchestrator.py --config config/scraper_config_ind.json --resume

  # Run only discovery phase
  python youtube_orchestrator.py --config config/scraper_config_uk.json --discovery-only

  # Run only scraping phase with existing queue
  python youtube_orchestrator.py --scrape-only --queue data/queue/ind/mixed_India_20251208.json

  # Custom categories and locations
  python youtube_orchestrator.py --config config/scraper_config_eur.json --categories tech gaming --locations Paris Berlin
        """
    )
    
    parser.add_argument('--config', '-c', help='Path to region config file')
    parser.add_argument('--region', '-r', help='Region name (auto-detected from config if not specified)')
    parser.add_argument('--categories', nargs='+', help='Categories to discover')
    parser.add_argument('--locations', nargs='+', help='Locations to search')
    parser.add_argument('--headless', action='store_true', default=True, help='Run browser headless')
    parser.add_argument('--no-headless', action='store_false', dest='headless', help='Show browser window')
    parser.add_argument('--resume', action='store_true', help='Resume from existing state file')
    parser.add_argument('--state-file', help='Specific state file to use')
    parser.add_argument('--discovery-only', action='store_true', help='Only run discovery phase')
    parser.add_argument('--scrape-only', action='store_true', help='Only run scraping phase')
    parser.add_argument('--queue', '-q', help='Queue file to scrape (for --scrape-only)')
    
    args = parser.parse_args()
    
    # Validate arguments
    if args.scrape_only and not args.queue:
        parser.error("--scrape-only requires --queue argument")
    
    # Create orchestrator
    orchestrator = ResilientOrchestrator(
        config_path=args.config,
        region=args.region,
        state_file=args.state_file if args.resume else None,
        headless=args.headless,
        categories=args.categories,
        locations=args.locations,
        resume=args.resume
    )
    
    # Run appropriate phase(s)
    if args.discovery_only:
        queue_file = await orchestrator.run_discovery()
        if queue_file:
            print(f"\nQueue file created: {queue_file}")
            print(f"To scrape, run: python youtube_orchestrator.py --scrape-only --queue {queue_file}")
    elif args.scrape_only:
        stats = await orchestrator.run_scraping(args.queue)
        print(f"\nScraping complete: {stats}")
    else:
        results = await orchestrator.run()
        return results


if __name__ == '__main__':
    asyncio.run(main())
