"""
Remote Scanner for Media File Organizer
Scans remotes for new files and checks upload completion
"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Set
from dataclasses import dataclass
from pathlib import PurePosixPath

from rclone_wrapper import RcloneWrapper, RemoteFile
from database import Database
from config_loader import Config

logger = logging.getLogger(__name__)


@dataclass
class ScannedFile:
    """Represents a file found during scanning."""
    remote: str
    path: str
    name: str
    size: int
    is_stable: bool
    is_processed: bool


class Scanner:
    """
    Scans remote storages for new media files.
    Tracks file stability to detect completed uploads.
    """
    
    def __init__(self, config: Config, rclone: RcloneWrapper, db: Database):
        """
        Initialize scanner.
        
        Args:
            config: Configuration object
            rclone: RcloneWrapper instance
            db: Database instance
        """
        self.config = config
        self.rclone = rclone
        self.db = db
        self.video_extensions = set(config.video_extensions)
    
    def scan_remote(self, remote: str) -> List[ScannedFile]:
        """
        Scan a remote for media files.
        
        Args:
            remote: Remote name to scan
        
        Returns:
            List of ScannedFile objects
        """
        logger.info(f"Scanning remote: {remote}")
        
        try:
            # Get all files recursively
            files = self.rclone.list_files(remote, "", recursive=True)
            logger.debug(f"Found {len(files)} items in {remote}")
        except Exception as e:
            logger.error(f"Failed to scan remote {remote}: {e}")
            return []
        
        scanned_files = []
        
        for file in files:
            # Skip directories
            if file.is_dir:
                continue
            
            # Skip non-video files
            if not self._is_video_file(file.name):
                continue
            
            # Check if already processed
            is_processed = self.db.is_file_processed(remote, file.path)
            if is_processed:
                logger.debug(f"Skipping already processed: {remote}:{file.path}")
                continue
            
            # Update stability tracking
            stability_info = self.db.update_file_stability(remote, file.path, file.size)
            
            # Check if file is stable (size unchanged for configured period)
            is_stable = self._check_stability(remote, file.path, stability_info)
            
            scanned_files.append(ScannedFile(
                remote=remote,
                path=file.path,
                name=file.name,
                size=file.size,
                is_stable=is_stable,
                is_processed=is_processed
            ))
        
        stable_count = sum(1 for f in scanned_files if f.is_stable)
        logger.info(f"Remote {remote}: {len(scanned_files)} new files, {stable_count} stable")
        
        return scanned_files
    
    def scan_all_remotes(self) -> List[ScannedFile]:
        """
        Scan all configured remotes.
        
        Returns:
            Combined list of ScannedFile objects from all remotes
        """
        all_files = []
        
        for remote in self.config.scan_remotes:
            try:
                files = self.scan_remote(remote)
                all_files.extend(files)
            except Exception as e:
                logger.error(f"Error scanning remote {remote}: {e}")
                continue
        
        return all_files
    
    def get_stable_files(self) -> List[ScannedFile]:
        """
        Get only stable files ready for processing.
        
        Returns:
            List of stable ScannedFile objects
        """
        all_files = self.scan_all_remotes()
        return [f for f in all_files if f.is_stable and not f.is_processed]
    
    def _is_video_file(self, filename: str) -> bool:
        """Check if filename is a video file."""
        path = PurePosixPath(filename)
        return path.suffix.lower() in self.video_extensions
    
    def _check_stability(self, remote: str, path: str, 
                         stability_info: Dict[str, Any]) -> bool:
        """
        Check if a file is stable (upload complete).
        
        A file is considered stable if its size hasn't changed
        for the configured stability period.
        """
        if stability_info.get("is_new"):
            return False
        
        last_change_str = stability_info.get("last_size_change")
        if not last_change_str:
            return False
        
        try:
            last_change = datetime.fromisoformat(last_change_str)
            stability_threshold = timedelta(seconds=self.config.stability_check_seconds)
            
            if datetime.now() - last_change >= stability_threshold:
                # Mark as stable in database
                self.db.mark_file_stable(remote, path)
                return True
        except (ValueError, TypeError):
            pass
        
        return False
    
    def find_folders_with_media(self, remote: str) -> List[str]:
        """
        Find folders that contain media files.
        Used for detecting extracted series folders.
        
        Args:
            remote: Remote to scan
        
        Returns:
            List of folder paths containing media
        """
        try:
            files = self.rclone.list_files(remote, "", recursive=True)
        except Exception as e:
            logger.error(f"Failed to list files in {remote}: {e}")
            return []
        
        folders_with_media: Set[str] = set()
        
        for file in files:
            if file.is_dir:
                continue
            
            if self._is_video_file(file.name):
                # Get parent folder
                path = PurePosixPath(file.path)
                if path.parent != PurePosixPath('.'):
                    folders_with_media.add(str(path.parent))
        
        return list(folders_with_media)
    
    def get_files_in_folder(self, remote: str, folder: str) -> List[RemoteFile]:
        """
        Get all files within a specific folder.
        
        Args:
            remote: Remote name
            folder: Folder path
        
        Returns:
            List of RemoteFile objects
        """
        try:
            files = self.rclone.list_files(remote, folder, recursive=True)
            return [f for f in files if not f.is_dir]
        except Exception as e:
            logger.error(f"Failed to list files in {remote}:{folder}: {e}")
            return []
