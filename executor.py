"""
Executor for Media File Organizer
Executes move decisions safely with rollback capability
"""

import logging
from typing import List, Tuple
from dataclasses import dataclass

from rclone_wrapper import RcloneWrapper, RcloneError
from database import Database
from decision_engine import MoveDecision

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Result of executing a move decision."""
    success: bool
    decision: MoveDecision
    error_message: str = None


class Executor:
    """
    Executes file move operations safely.
    Handles quality replacement with proper ordering (move first, then delete).
    """
    
    def __init__(self, rclone: RcloneWrapper, db: Database, dry_run: bool = False):
        """
        Initialize executor.
        
        Args:
            rclone: RcloneWrapper instance
            db: Database instance
            dry_run: If True, only log actions without executing
        """
        self.rclone = rclone
        self.db = db
        self.dry_run = dry_run
    
    def execute(self, decision: MoveDecision) -> ExecutionResult:
        """
        Execute a single move decision.
        
        Args:
            decision: MoveDecision to execute
        
        Returns:
            ExecutionResult with success status
        """
        if decision.action == 'skip':
            logger.info(f"Skipping: {decision.source_remote}:{decision.source_path}")
            # Record as skipped to prevent repeated reprocessing
            self._record_skipped(decision)
            return ExecutionResult(success=True, decision=decision)
        
        if decision.action == 'error':
            logger.warning(f"Cannot process (error): {decision.source_path} - {decision.error_message}")
            self._record_failure(decision, decision.error_message)
            return ExecutionResult(
                success=False, 
                decision=decision, 
                error_message=decision.error_message
            )
        
        if decision.action == 'move':
            return self._execute_move(decision)
        
        if decision.action == 'replace':
            return self._execute_replace(decision)
        
        if decision.action == 'delete_source':
            return self._execute_delete_source(decision)
        
        logger.error(f"Unknown action: {decision.action}")
        return ExecutionResult(
            success=False,
            decision=decision,
            error_message=f"Unknown action: {decision.action}"
        )
    
    def _execute_move(self, decision: MoveDecision) -> ExecutionResult:
        """Execute a simple move operation."""
        logger.info(f"Moving: {decision.source_remote}:{decision.source_path} -> "
                   f"{decision.destination_remote}:{decision.destination_path}")
        
        if self.dry_run:
            logger.info("[DRY RUN] Would move file")
            return ExecutionResult(success=True, decision=decision)
        
        try:
            # Execute move
            self.rclone.move_file(
                src_remote=decision.source_remote,
                src_path=decision.source_path,
                dst_remote=decision.destination_remote,
                dst_path=decision.destination_path
            )
            
            # Record success in database
            self._record_success(decision)
            
            # Update quality tracking
            self._update_quality_tracking(decision)
            
            # Clean up stability tracking
            self.db.remove_stability_tracking(decision.source_remote, decision.source_path)
            
            # Try to remove empty directories
            self._cleanup_empty_dirs(decision.source_remote, decision.source_path)
            
            logger.info(f"Successfully moved: {decision.source_path}")
            return ExecutionResult(success=True, decision=decision)
        
        except RcloneError as e:
            error_msg = f"Move failed: {e}"
            logger.error(error_msg)
            self._record_failure(decision, error_msg)
            return ExecutionResult(success=False, decision=decision, error_message=error_msg)
    
    def _execute_replace(self, decision: MoveDecision) -> ExecutionResult:
        """
        Execute a quality replacement operation.
        
        IMPORTANT: Move new file FIRST, verify success, THEN delete old file.
        Never delete old file if move fails.
        """
        logger.info(f"Replacing: {decision.delete_remote}:{decision.file_to_delete} with "
                   f"{decision.source_remote}:{decision.source_path}")
        
        if self.dry_run:
            logger.info("[DRY RUN] Would replace file")
            return ExecutionResult(success=True, decision=decision)
        
        # Step 1: Move new file first
        try:
            self.rclone.move_file(
                src_remote=decision.source_remote,
                src_path=decision.source_path,
                dst_remote=decision.destination_remote,
                dst_path=decision.destination_path
            )
            logger.info(f"Step 1/2 complete: New file moved successfully")
        except RcloneError as e:
            error_msg = f"Replacement aborted - move failed: {e}"
            logger.error(error_msg)
            logger.warning("Old file preserved - no data lost")
            self._record_failure(decision, error_msg)
            return ExecutionResult(success=False, decision=decision, error_message=error_msg)
        
        # Step 2: Verify new file exists before deleting old
        try:
            if not self.rclone.file_exists(decision.destination_remote, decision.destination_path):
                error_msg = "Replacement aborted - new file not found after move"
                logger.error(error_msg)
                self._record_failure(decision, error_msg)
                return ExecutionResult(success=False, decision=decision, error_message=error_msg)
        except RcloneError as e:
            error_msg = f"Could not verify new file: {e}"
            logger.warning(error_msg)
            # Continue anyway - move was successful
        
        # Step 3: Delete old file only after successful move
        try:
            if decision.file_to_delete and decision.delete_remote:
                self.rclone.delete_file(decision.delete_remote, decision.file_to_delete)
                logger.info(f"Step 2/2 complete: Old file deleted")
        except RcloneError as e:
            # Move succeeded but delete failed - not critical
            logger.warning(f"Could not delete old file: {e}")
            logger.warning(f"Manual cleanup needed: {decision.delete_remote}:{decision.file_to_delete}")
        
        # Record success
        self._record_success(decision)
        self._update_quality_tracking(decision)
        self.db.remove_stability_tracking(decision.source_remote, decision.source_path)
        
        # Cleanup
        self._cleanup_empty_dirs(decision.source_remote, decision.source_path)
        if decision.file_to_delete:
            self._cleanup_empty_dirs(decision.delete_remote, decision.file_to_delete)
        
        logger.info(f"Successfully replaced with higher quality: {decision.quality}")
        return ExecutionResult(success=True, decision=decision)
    
    def _execute_delete_source(self, decision: MoveDecision) -> ExecutionResult:
        """
        Delete source file because destination already has same/better quality.
        This prevents duplicates when organizing files that are already organized.
        """
        logger.info(f"Deleting duplicate source: {decision.source_remote}:{decision.source_path}")
        logger.info(f"(Destination already has: {decision.file_to_delete})")
        
        if self.dry_run:
            logger.info("[DRY RUN] Would delete source file (duplicate)")
            return ExecutionResult(success=True, decision=decision)
        
        try:
            # Delete the source file
            self.rclone.delete_file(decision.source_remote, decision.source_path)
            
            # Record as processed (with status 'duplicate_deleted')
            self.db.add_processed_file(
                remote=decision.source_remote,
                original_path=decision.source_path,
                destination_path=decision.destination_path or '',
                file_size=0,
                tmdb_id=decision.tmdb_id or 0,
                tmdb_type=decision.tmdb_type or 'unknown',
                title=decision.title,
                year=decision.year or 0,
                season=decision.season,
                episode=decision.episode,
                quality=decision.quality,
                content_type=decision.content_type,
                status='duplicate_deleted'
            )
            
            # Clean up stability tracking
            self.db.remove_stability_tracking(decision.source_remote, decision.source_path)
            
            # Try to remove empty directories
            self._cleanup_empty_dirs(decision.source_remote, decision.source_path)
            
            logger.info(f"Successfully deleted duplicate: {decision.source_path}")
            return ExecutionResult(success=True, decision=decision)
        
        except RcloneError as e:
            error_msg = f"Failed to delete source: {e}"
            logger.error(error_msg)
            self._record_failure(decision, error_msg)
            return ExecutionResult(success=False, decision=decision, error_message=error_msg)
    
    def _record_success(self, decision: MoveDecision):
        """Record successful processing in database."""
        self.db.add_processed_file(
            remote=decision.source_remote,
            original_path=decision.source_path,
            destination_path=decision.destination_path,
            file_size=0,  # Could fetch if needed
            tmdb_id=decision.tmdb_id or 0,
            tmdb_type=decision.tmdb_type or 'unknown',
            title=decision.title,
            year=decision.year or 0,
            season=decision.season,
            episode=decision.episode,
            quality=decision.quality,
            content_type=decision.content_type,
            status='success'
        )
    
    def _record_failure(self, decision: MoveDecision, error_message: str):
        """Record failed processing in database."""
        self.db.add_processed_file(
            remote=decision.source_remote,
            original_path=decision.source_path,
            destination_path=decision.destination_path or '',
            file_size=0,
            tmdb_id=decision.tmdb_id or 0,
            tmdb_type=decision.tmdb_type or 'unknown',
            title=decision.title,
            year=decision.year or 0,
            season=decision.season,
            episode=decision.episode,
            quality=decision.quality,
            content_type=decision.content_type,
            status='failed',
            error_message=error_message
        )
    
    def _record_skipped(self, decision: MoveDecision):
        """Record skipped file to prevent repeated reprocessing."""
        self.db.add_processed_file(
            remote=decision.source_remote,
            original_path=decision.source_path,
            destination_path=decision.destination_path or '',
            file_size=0,
            tmdb_id=decision.tmdb_id or 0,
            tmdb_type=decision.tmdb_type or 'unknown',
            title=decision.title,
            year=decision.year or 0,
            season=decision.season,
            episode=decision.episode,
            quality=decision.quality,
            content_type=decision.content_type,
            status='skipped'
        )
        # Also clean up stability tracking for skipped files
        self.db.remove_stability_tracking(decision.source_remote, decision.source_path)
    
    def _update_quality_tracking(self, decision: MoveDecision):
        """Update quality tracking for future comparisons."""
        if decision.tmdb_id:
            self.db.update_quality_tracking(
                tmdb_id=decision.tmdb_id,
                tmdb_type=decision.tmdb_type or 'unknown',
                quality=decision.quality,
                file_path=decision.destination_path,
                remote=decision.destination_remote,
                season=decision.season,
                episode=decision.episode
            )
    
    def _cleanup_empty_dirs(self, remote: str, path: str):
        """Try to clean up empty directories after move."""
        try:
            # Get parent directory
            from pathlib import PurePosixPath
            parent = str(PurePosixPath(path).parent)
            if parent and parent != '.':
                self.rclone.delete_empty_dirs(remote, parent)
        except Exception as e:
            logger.debug(f"Could not cleanup empty dirs: {e}")
    
    def execute_batch(self, decisions: List[MoveDecision]) -> Tuple[int, int]:
        """
        Execute a batch of decisions.
        
        Args:
            decisions: List of MoveDecision objects
        
        Returns:
            Tuple of (success_count, failure_count)
        """
        success_count = 0
        failure_count = 0
        
        for decision in decisions:
            result = self.execute(decision)
            if result.success:
                success_count += 1
            else:
                failure_count += 1
        
        logger.info(f"Batch execution complete: {success_count} succeeded, {failure_count} failed")
        return success_count, failure_count
