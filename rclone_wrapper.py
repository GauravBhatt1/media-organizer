"""
rclone CLI Wrapper for Media File Organizer
Server-side only operations - NO local downloads or mounts
"""

import subprocess
import json
import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RemoteFile:
    """Represents a file on a remote."""
    path: str
    name: str
    size: int
    is_dir: bool
    mod_time: str


class RcloneError(Exception):
    """Custom exception for rclone errors."""
    pass


class RcloneWrapper:
    """
    Wrapper for rclone CLI operations.
    Only uses server-side operations - no local downloads or mounts.
    """
    
    def __init__(self, timeout: int = 300):
        """
        Initialize rclone wrapper.
        
        Args:
            timeout: Command timeout in seconds (default 5 minutes)
        """
        self.timeout = timeout
        self._verify_rclone()
    
    def _verify_rclone(self):
        """Verify rclone is installed and accessible."""
        try:
            result = subprocess.run(
                ["rclone", "version"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode != 0:
                raise RcloneError("rclone command failed")
            
            version_line = result.stdout.split('\n')[0]
            logger.info(f"rclone verified: {version_line}")
        except FileNotFoundError:
            raise RcloneError("rclone is not installed or not in PATH")
        except subprocess.TimeoutExpired:
            raise RcloneError("rclone version check timed out")
    
    def _run_command(self, args: List[str], timeout: Optional[int] = None) -> Tuple[str, str]:
        """
        Run an rclone command and return stdout/stderr.
        
        Args:
            args: Command arguments (without 'rclone' prefix)
            timeout: Optional timeout override
        
        Returns:
            Tuple of (stdout, stderr)
        
        Raises:
            RcloneError: If command fails
        """
        cmd = ["rclone"] + args
        cmd_str = " ".join(cmd)
        logger.debug(f"Running: {cmd_str}")
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout
            )
            
            if result.returncode != 0:
                error_msg = result.stderr.strip() or result.stdout.strip()
                logger.error(f"rclone command failed: {error_msg}")
                raise RcloneError(f"Command failed: {error_msg}")
            
            return result.stdout, result.stderr
        
        except subprocess.TimeoutExpired:
            logger.error(f"rclone command timed out: {cmd_str}")
            raise RcloneError(f"Command timed out after {timeout or self.timeout}s")
    
    # ==================== Listing Operations ====================
    
    def list_files(self, remote: str, path: str = "", recursive: bool = True) -> List[RemoteFile]:
        """
        List files in a remote path.
        
        Args:
            remote: Remote name (e.g., 'movies')
            path: Path within remote (empty for root)
            recursive: Whether to list recursively
        
        Returns:
            List of RemoteFile objects
        """
        remote_path = f"{remote}:{path}" if path else f"{remote}:"
        
        args = ["lsjson", remote_path]
        if recursive:
            args.append("--recursive")
        
        stdout, _ = self._run_command(args)
        
        if not stdout.strip():
            return []
        
        try:
            items = json.loads(stdout)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse rclone output: {e}")
            return []
        
        files = []
        for item in items:
            files.append(RemoteFile(
                path=item.get("Path", ""),
                name=item.get("Name", ""),
                size=item.get("Size", 0),
                is_dir=item.get("IsDir", False),
                mod_time=item.get("ModTime", "")
            ))
        
        logger.debug(f"Listed {len(files)} items in {remote_path}")
        return files
    
    def list_files_flat(self, remote: str, path: str = "", recursive: bool = True) -> List[str]:
        """
        List file paths only (faster than full listing).
        
        Args:
            remote: Remote name
            path: Path within remote
            recursive: Whether to list recursively
        
        Returns:
            List of file paths
        """
        remote_path = f"{remote}:{path}" if path else f"{remote}:"
        
        args = ["lsf", remote_path, "--files-only"]
        if recursive:
            args.append("--recursive")
        
        stdout, _ = self._run_command(args)
        
        if not stdout.strip():
            return []
        
        return [line.strip() for line in stdout.strip().split('\n') if line.strip()]
    
    def get_file_size(self, remote: str, path: str) -> int:
        """
        Get size of a specific file.
        
        Args:
            remote: Remote name
            path: File path
        
        Returns:
            File size in bytes
        """
        remote_path = f"{remote}:{path}"
        
        args = ["lsjson", remote_path]
        stdout, _ = self._run_command(args)
        
        try:
            items = json.loads(stdout)
            if items:
                return items[0].get("Size", 0)
        except (json.JSONDecodeError, IndexError):
            pass
        
        return 0
    
    def file_exists(self, remote: str, path: str) -> bool:
        """Check if a file exists on remote."""
        try:
            remote_path = f"{remote}:{path}"
            args = ["lsjson", remote_path]
            stdout, _ = self._run_command(args, timeout=30)
            items = json.loads(stdout) if stdout.strip() else []
            return len(items) > 0
        except RcloneError:
            return False
    
    def dir_exists(self, remote: str, path: str) -> bool:
        """Check if a directory exists on remote."""
        try:
            remote_path = f"{remote}:{path}"
            args = ["lsf", remote_path, "--dirs-only", "--max-depth", "1"]
            stdout, _ = self._run_command(args, timeout=30)
            # If command succeeds, directory exists (even if empty)
            return True
        except RcloneError:
            return False
    
    # ==================== Move/Rename Operations ====================
    
    def move_file(self, src_remote: str, src_path: str, 
                  dst_remote: str, dst_path: str) -> bool:
        """
        Move a file from source to destination (server-side).
        Creates destination directories automatically.
        
        Args:
            src_remote: Source remote name
            src_path: Source file path
            dst_remote: Destination remote name
            dst_path: Destination file path
        
        Returns:
            True if successful
        """
        src = f"{src_remote}:{src_path}"
        dst = f"{dst_remote}:{dst_path}"
        
        logger.info(f"Moving: {src} -> {dst}")
        
        try:
            # Use moveto for single file move with rename
            args = ["moveto", src, dst]
            self._run_command(args)
            logger.info(f"Successfully moved: {src} -> {dst}")
            return True
        except RcloneError as e:
            logger.error(f"Failed to move {src} -> {dst}: {e}")
            raise
    
    def move_directory(self, src_remote: str, src_path: str,
                       dst_remote: str, dst_path: str) -> bool:
        """
        Move a directory from source to destination (server-side).
        
        Args:
            src_remote: Source remote name
            src_path: Source directory path
            dst_remote: Destination remote name
            dst_path: Destination directory path
        
        Returns:
            True if successful
        """
        src = f"{src_remote}:{src_path}"
        dst = f"{dst_remote}:{dst_path}"
        
        logger.info(f"Moving directory: {src} -> {dst}")
        
        try:
            args = ["move", src, dst]
            self._run_command(args)
            logger.info(f"Successfully moved directory: {src} -> {dst}")
            return True
        except RcloneError as e:
            logger.error(f"Failed to move directory {src} -> {dst}: {e}")
            raise
    
    # ==================== Delete Operations ====================
    
    def delete_file(self, remote: str, path: str) -> bool:
        """
        Delete a single file.
        
        Args:
            remote: Remote name
            path: File path
        
        Returns:
            True if successful
        """
        remote_path = f"{remote}:{path}"
        logger.info(f"Deleting file: {remote_path}")
        
        try:
            args = ["deletefile", remote_path]
            self._run_command(args)
            logger.info(f"Successfully deleted: {remote_path}")
            return True
        except RcloneError as e:
            logger.error(f"Failed to delete {remote_path}: {e}")
            raise
    
    def delete_empty_dirs(self, remote: str, path: str = "") -> bool:
        """
        Remove empty directories recursively.
        
        Args:
            remote: Remote name
            path: Starting path
        
        Returns:
            True if successful
        """
        remote_path = f"{remote}:{path}" if path else f"{remote}:"
        logger.info(f"Removing empty directories in: {remote_path}")
        
        try:
            args = ["rmdirs", remote_path]
            self._run_command(args)
            logger.info(f"Successfully cleaned empty directories in: {remote_path}")
            return True
        except RcloneError as e:
            logger.warning(f"Failed to remove empty dirs in {remote_path}: {e}")
            return False
    
    # ==================== Utility Operations ====================
    
    def get_remote_space(self, remote: str) -> Dict[str, int]:
        """
        Get space information for a remote.
        
        Args:
            remote: Remote name
        
        Returns:
            Dict with 'total', 'used', 'free' in bytes
        """
        try:
            args = ["about", f"{remote}:", "--json"]
            stdout, _ = self._run_command(args, timeout=60)
            
            data = json.loads(stdout)
            return {
                "total": data.get("total", 0),
                "used": data.get("used", 0),
                "free": data.get("free", 0)
            }
        except (RcloneError, json.JSONDecodeError) as e:
            logger.warning(f"Could not get space info for {remote}: {e}")
            return {"total": 0, "used": 0, "free": 0}
    
    def is_remote_available(self, remote: str) -> bool:
        """Check if a remote is accessible."""
        try:
            args = ["lsf", f"{remote}:", "--max-depth", "1"]
            self._run_command(args, timeout=30)
            return True
        except RcloneError:
            return False
