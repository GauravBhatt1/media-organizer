#!/usr/bin/env python3
"""
Media File Organizer - Main Entry Point
Organizes cloud media files for Jellyfin using rclone
"""

import os
import sys
import time
import signal
import argparse
import logging
from datetime import datetime
from pathlib import Path

# Add current directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config_loader import Config
from database import Database
from logger_config import setup_logging
from rclone_wrapper import RcloneWrapper, RcloneError
from filename_parser import FilenameParser
from tmdb_matcher import TMDBMatcher, TMDBError
from scanner import Scanner
from decision_engine import DecisionEngine
from executor import Executor

logger = logging.getLogger(__name__)


class MediaOrganizer:
    """
    Main orchestrator for media file organization.
    Coordinates scanning, decision making, and execution.
    """
    
    def __init__(self, config_path: str = "config.yaml", dry_run: bool = False):
        """
        Initialize the media organizer.
        
        Args:
            config_path: Path to configuration file
            dry_run: If True, only log actions without executing
        """
        self.dry_run = dry_run
        self.running = False
        self._shutdown_requested = False
        
        # Load configuration
        self.config = Config(config_path)
        
        # Setup logging
        setup_logging(
            level=self.config.logging_level,
            log_file=self.config.log_file,
            max_size_mb=self.config.log_max_size_mb,
            backup_count=self.config.log_backup_count
        )
        
        logger.info("=" * 60)
        logger.info("Media File Organizer Starting")
        logger.info("=" * 60)
        
        if dry_run:
            logger.warning("DRY RUN MODE - No files will be moved")
        
        # Initialize components
        self._init_components()
    
    def _init_components(self):
        """Initialize all components."""
        # Database
        logger.info(f"Initializing database: {self.config.database_path}")
        self.db = Database(self.config.database_path)
        
        # rclone wrapper
        logger.info("Initializing rclone wrapper")
        try:
            self.rclone = RcloneWrapper()
        except RcloneError as e:
            logger.critical(f"Failed to initialize rclone: {e}")
            raise
        
        # Verify remotes are accessible
        self._verify_remotes()
        
        # Filename parser
        self.parser = FilenameParser(self.config.video_extensions)
        
        # TMDB matcher
        logger.info("Initializing TMDB matcher")
        try:
            self.tmdb = TMDBMatcher(
                api_key=self.config.tmdb_api_key,
                language=self.config.tmdb_language,
                include_adult=self.config.include_adult
            )
            if not self.tmdb.verify_api_key():
                raise TMDBError("TMDB API key verification failed")
        except TMDBError as e:
            logger.critical(f"Failed to initialize TMDB: {e}")
            raise
        
        # Scanner
        self.scanner = Scanner(self.config, self.rclone, self.db)
        
        # Decision engine (with rclone for destination checks)
        self.decision_engine = DecisionEngine(
            self.config, self.db, self.parser, self.tmdb, self.rclone
        )
        
        # Executor
        self.executor = Executor(self.rclone, self.db, dry_run=self.dry_run)
        
        logger.info("All components initialized successfully")
    
    def _verify_remotes(self):
        """Verify all configured remotes are accessible."""
        logger.info("Verifying remote access...")
        
        for remote in self.config.scan_remotes:
            if self.rclone.is_remote_available(remote):
                logger.info(f"  {remote}: OK")
            else:
                logger.warning(f"  {remote}: NOT ACCESSIBLE")
    
    def _setup_signal_handlers(self):
        """Setup graceful shutdown handlers."""
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, shutting down gracefully...")
            self._shutdown_requested = True
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    
    def run_once(self) -> tuple:
        """
        Run a single scan and processing cycle.
        
        Returns:
            Tuple of (files_processed, files_failed)
        """
        logger.info("-" * 40)
        logger.info(f"Starting scan at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Get stable files ready for processing
        stable_files = self.scanner.get_stable_files()
        
        if not stable_files:
            logger.info("No stable files found for processing")
            return 0, 0
        
        logger.info(f"Found {len(stable_files)} stable files to process")
        
        # Make decisions and execute
        success_count = 0
        failure_count = 0
        
        for file in stable_files:
            if self._shutdown_requested:
                logger.info("Shutdown requested, stopping processing")
                break
            
            try:
                # Make decision
                decision = self.decision_engine.decide(file.remote, file.path)
                
                # Execute decision
                result = self.executor.execute(decision)
                
                if result.success:
                    success_count += 1
                else:
                    failure_count += 1
            
            except Exception as e:
                logger.error(f"Error processing {file.remote}:{file.path}: {e}")
                failure_count += 1
        
        logger.info(f"Scan complete: {success_count} processed, {failure_count} failed")
        return success_count, failure_count
    
    def run_daemon(self):
        """
        Run as a daemon, continuously scanning at configured intervals.
        """
        self._setup_signal_handlers()
        self.running = True
        
        interval_seconds = self.config.scan_interval_minutes * 60
        
        logger.info(f"Starting daemon mode (scan every {self.config.scan_interval_minutes} minutes)")
        
        # Run immediately on startup if configured
        if self.config.run_on_startup:
            logger.info("Running initial scan on startup")
            try:
                self.run_once()
            except Exception as e:
                logger.error(f"Error in initial scan: {e}", exc_info=True)
        
        while self.running and not self._shutdown_requested:
            # Wait for next scan interval
            logger.info(f"Next scan in {self.config.scan_interval_minutes} minutes")
            
            # Sleep in small increments to allow for quick shutdown
            sleep_start = time.time()
            while time.time() - sleep_start < interval_seconds:
                if self._shutdown_requested:
                    break
                time.sleep(1)
            
            if self._shutdown_requested:
                break
            
            # Run scan with error handling
            try:
                self.run_once()
            except Exception as e:
                logger.error(f"Error in scan cycle: {e}", exc_info=True)
                logger.info("Will retry on next scan cycle")
        
        self.shutdown()
    
    def shutdown(self):
        """Cleanup and shutdown."""
        logger.info("Shutting down...")
        self.running = False
        
        if hasattr(self, 'db'):
            self.db.close()
        
        logger.info("Shutdown complete")
    
    def show_status(self):
        """Show current status and statistics."""
        print("\n=== Media Organizer Status ===\n")
        
        # Show remote status
        print("Remotes:")
        for remote in self.config.scan_remotes:
            status = "OK" if self.rclone.is_remote_available(remote) else "UNAVAILABLE"
            content_type = self.config.get_remote_type(remote)
            print(f"  {remote}: {status} ({content_type})")
        
        # Show pending files
        print("\nPending Files:")
        stable_files = self.scanner.get_stable_files()
        if stable_files:
            for file in stable_files[:10]:
                print(f"  {file.remote}:{file.path} ({file.size:,} bytes)")
            if len(stable_files) > 10:
                print(f"  ... and {len(stable_files) - 10} more")
        else:
            print("  No files pending")
        
        # Show failed files
        failed = self.db.get_failed_files()
        if failed:
            print(f"\nFailed Files ({len(failed)}):")
            for f in failed[:5]:
                print(f"  {f['remote']}:{f['original_path']}")
                print(f"    Error: {f['error_message']}")
        
        print()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Media File Organizer for Jellyfin"
    )
    
    # Environment variable defaults for Docker
    default_config = os.getenv('ORGANIZER_CONFIG', 'config.yaml')
    default_dry_run = os.getenv('DRY_RUN', 'false').lower() == 'true'
    
    parser.add_argument(
        '-c', '--config',
        default=default_config,
        help=f'Path to configuration file (default: {default_config})'
    )
    
    parser.add_argument(
        '-d', '--daemon',
        action='store_true',
        help='Run as daemon (continuous scanning)'
    )
    
    parser.add_argument(
        '-n', '--dry-run',
        action='store_true',
        default=default_dry_run,
        help='Dry run mode - log actions without executing'
    )
    
    parser.add_argument(
        '-s', '--status',
        action='store_true',
        help='Show current status and exit'
    )
    
    parser.add_argument(
        '-o', '--once',
        action='store_true',
        help='Run once and exit'
    )
    
    args = parser.parse_args()
    
    try:
        organizer = MediaOrganizer(
            config_path=args.config,
            dry_run=args.dry_run
        )
        
        if args.status:
            organizer.show_status()
        elif args.once:
            organizer.run_once()
            organizer.shutdown()
        elif args.daemon:
            organizer.run_daemon()
        else:
            # Default: run once
            organizer.run_once()
            organizer.shutdown()
    
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
