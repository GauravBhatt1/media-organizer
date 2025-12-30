"""
SQLite Database Layer for Media File Organizer
Tracks processed files, TMDB matches, and file states
"""

import sqlite3
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_path: str = "organizer.db"):
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        self._init_db()
    
    def _init_db(self):
        """Initialize database and create tables if they don't exist."""
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        
        cursor = self.conn.cursor()
        
        # Processed files table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS processed_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                remote TEXT NOT NULL,
                original_path TEXT NOT NULL,
                destination_path TEXT,
                file_size INTEGER,
                tmdb_id INTEGER,
                tmdb_type TEXT,
                title TEXT,
                year INTEGER,
                season INTEGER,
                episode INTEGER,
                quality TEXT,
                content_type TEXT,
                status TEXT DEFAULT 'pending',
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                processed_at TIMESTAMP,
                UNIQUE(remote, original_path)
            )
        """)
        
        # File stability tracking (for detecting completed uploads)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS file_stability (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                remote TEXT NOT NULL,
                path TEXT NOT NULL,
                file_size INTEGER,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_size_change TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_stable INTEGER DEFAULT 0,
                UNIQUE(remote, path)
            )
        """)
        
        # Quality tracking for replacement logic
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS quality_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tmdb_id INTEGER NOT NULL,
                tmdb_type TEXT NOT NULL,
                season INTEGER,
                episode INTEGER,
                quality TEXT NOT NULL,
                file_path TEXT NOT NULL,
                remote TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tmdb_id, tmdb_type, season, episode)
            )
        """)
        
        # Create indexes for faster lookups
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_processed_remote_path ON processed_files(remote, original_path)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_stability_remote_path ON file_stability(remote, path)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_quality_tmdb ON quality_tracking(tmdb_id, tmdb_type)")
        
        self.conn.commit()
        logger.info(f"Database initialized at {self.db_path}")
    
    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            logger.info("Database connection closed")
    
    # ==================== File Stability Methods ====================
    
    def update_file_stability(self, remote: str, path: str, file_size: int) -> Dict[str, Any]:
        """
        Update file stability tracking. Returns stability info.
        A file is considered stable if its size hasn't changed for the configured period.
        """
        cursor = self.conn.cursor()
        now = datetime.now().isoformat()
        
        # Check if file exists in tracking
        cursor.execute(
            "SELECT * FROM file_stability WHERE remote = ? AND path = ?",
            (remote, path)
        )
        row = cursor.fetchone()
        
        if row is None:
            # New file - insert
            cursor.execute("""
                INSERT INTO file_stability (remote, path, file_size, first_seen, last_checked, last_size_change)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (remote, path, file_size, now, now, now))
            self.conn.commit()
            return {
                "is_new": True,
                "is_stable": False,
                "first_seen": now,
                "last_size_change": now
            }
        
        # File exists - check if size changed
        if row["file_size"] != file_size:
            # Size changed - update
            cursor.execute("""
                UPDATE file_stability 
                SET file_size = ?, last_checked = ?, last_size_change = ?, is_stable = 0
                WHERE remote = ? AND path = ?
            """, (file_size, now, now, remote, path))
            self.conn.commit()
            return {
                "is_new": False,
                "is_stable": False,
                "first_seen": row["first_seen"],
                "last_size_change": now
            }
        
        # Size unchanged - just update last_checked
        cursor.execute("""
            UPDATE file_stability 
            SET last_checked = ?
            WHERE remote = ? AND path = ?
        """, (now, remote, path))
        self.conn.commit()
        
        return {
            "is_new": False,
            "is_stable": False,  # Caller will determine based on time
            "first_seen": row["first_seen"],
            "last_size_change": row["last_size_change"]
        }
    
    def mark_file_stable(self, remote: str, path: str):
        """Mark a file as stable (upload complete)."""
        cursor = self.conn.cursor()
        cursor.execute("""
            UPDATE file_stability SET is_stable = 1 WHERE remote = ? AND path = ?
        """, (remote, path))
        self.conn.commit()
    
    def is_file_stable(self, remote: str, path: str) -> bool:
        """Check if a file is marked as stable."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT is_stable FROM file_stability WHERE remote = ? AND path = ?",
            (remote, path)
        )
        row = cursor.fetchone()
        return row is not None and row["is_stable"] == 1
    
    def get_file_stability_info(self, remote: str, path: str) -> Optional[Dict[str, Any]]:
        """Get stability info for a file."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM file_stability WHERE remote = ? AND path = ?",
            (remote, path)
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    
    def remove_stability_tracking(self, remote: str, path: str):
        """Remove file from stability tracking (after processing)."""
        cursor = self.conn.cursor()
        cursor.execute(
            "DELETE FROM file_stability WHERE remote = ? AND path = ?",
            (remote, path)
        )
        self.conn.commit()
    
    # ==================== Processed Files Methods ====================
    
    def is_file_processed(self, remote: str, path: str) -> bool:
        """Check if a file has already been processed (success or skipped)."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT status FROM processed_files 
            WHERE remote = ? AND original_path = ? AND status IN ('success', 'skipped')
        """, (remote, path))
        return cursor.fetchone() is not None
    
    def add_processed_file(self, remote: str, original_path: str, 
                           destination_path: str, file_size: int,
                           tmdb_id: int, tmdb_type: str, title: str,
                           year: int, season: Optional[int], episode: Optional[int],
                           quality: str, content_type: str, status: str = 'success',
                           error_message: Optional[str] = None):
        """Record a processed file."""
        cursor = self.conn.cursor()
        now = datetime.now().isoformat()
        
        cursor.execute("""
            INSERT OR REPLACE INTO processed_files 
            (remote, original_path, destination_path, file_size, tmdb_id, tmdb_type,
             title, year, season, episode, quality, content_type, status, error_message, processed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (remote, original_path, destination_path, file_size, tmdb_id, tmdb_type,
              title, year, season, episode, quality, content_type, status, error_message, now))
        self.conn.commit()
        logger.info(f"Recorded processed file: {remote}:{original_path} -> {destination_path}")
    
    def get_processed_file(self, remote: str, path: str) -> Optional[Dict[str, Any]]:
        """Get processed file info."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM processed_files WHERE remote = ? AND original_path = ?",
            (remote, path)
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    
    def get_failed_files(self) -> List[Dict[str, Any]]:
        """Get list of files that failed processing."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM processed_files WHERE status = 'failed'")
        return [dict(row) for row in cursor.fetchall()]
    
    # ==================== Quality Tracking Methods ====================
    
    def get_existing_quality(self, tmdb_id: int, tmdb_type: str, 
                              season: Optional[int] = None, 
                              episode: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Get existing quality info for a title/episode."""
        cursor = self.conn.cursor()
        
        if season is not None and episode is not None:
            cursor.execute("""
                SELECT * FROM quality_tracking 
                WHERE tmdb_id = ? AND tmdb_type = ? AND season = ? AND episode = ?
            """, (tmdb_id, tmdb_type, season, episode))
        else:
            cursor.execute("""
                SELECT * FROM quality_tracking 
                WHERE tmdb_id = ? AND tmdb_type = ? AND season IS NULL AND episode IS NULL
            """, (tmdb_id, tmdb_type))
        
        row = cursor.fetchone()
        return dict(row) if row else None
    
    def update_quality_tracking(self, tmdb_id: int, tmdb_type: str,
                                 quality: str, file_path: str, remote: str,
                                 season: Optional[int] = None,
                                 episode: Optional[int] = None):
        """Update quality tracking for a title/episode."""
        cursor = self.conn.cursor()
        now = datetime.now().isoformat()
        
        cursor.execute("""
            INSERT OR REPLACE INTO quality_tracking 
            (tmdb_id, tmdb_type, season, episode, quality, file_path, remote, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (tmdb_id, tmdb_type, season, episode, quality, file_path, remote, now))
        self.conn.commit()
    
    def remove_quality_tracking(self, tmdb_id: int, tmdb_type: str,
                                 season: Optional[int] = None,
                                 episode: Optional[int] = None):
        """Remove quality tracking entry."""
        cursor = self.conn.cursor()
        
        if season is not None and episode is not None:
            cursor.execute("""
                DELETE FROM quality_tracking 
                WHERE tmdb_id = ? AND tmdb_type = ? AND season = ? AND episode = ?
            """, (tmdb_id, tmdb_type, season, episode))
        else:
            cursor.execute("""
                DELETE FROM quality_tracking 
                WHERE tmdb_id = ? AND tmdb_type = ? AND season IS NULL AND episode IS NULL
            """, (tmdb_id, tmdb_type))
        
        self.conn.commit()
