"""
Scalable Background Job Queue for Video Generation

This module provides a database-backed job queue that can scale to handle
multiple concurrent users. Jobs are processed asynchronously by background
workers, allowing users to continue using the app while videos generate.

Architecture:
- Jobs stored in PostgreSQL (video_jobs table)
- Workers poll for pending jobs and process them
- Status updates are polled by frontend
- Can scale by running multiple workers
"""

import os
import json
import time
from datetime import datetime
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict
from enum import Enum

import psycopg2
from psycopg2.extras import RealDictCursor


DATABASE_URL = os.environ.get("DATABASE_URL")


class JobStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class VideoJob:
    id: int
    user_id: str
    project_id: int
    status: str
    quality_tier: str
    progress_current: int
    progress_total: int
    progress_message: Optional[str]
    result_url: Optional[str]
    error_message: Optional[str]
    job_data: Optional[Dict[str, Any]]
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]


class JobQueue:
    """
    Database-backed job queue for video generation.
    
    Designed to scale:
    - Multiple workers can poll for jobs
    - Uses row-level locking to prevent duplicate processing
    - Supports priority queuing (created_at order)
    """
    
    def __init__(self):
        self.db_url = DATABASE_URL
    
    def _get_connection(self):
        return psycopg2.connect(self.db_url, cursor_factory=RealDictCursor)
    
    def add_job(
        self,
        user_id: str,
        project_id: int,
        quality_tier: str = "good",
        job_data: Optional[Dict[str, Any]] = None
    ) -> int:
        """
        Add a new video generation job to the queue.
        
        Returns:
            The job ID
        """
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO video_jobs (user_id, project_id, quality_tier, job_data, status)
                    VALUES (%s, %s, %s, %s, 'pending')
                    RETURNING id
                """, (user_id, project_id, quality_tier, json.dumps(job_data or {})))
                job_id = cur.fetchone()['id']
                conn.commit()
                print(f"[JobQueue] Created job {job_id} for user {user_id}, quality={quality_tier}")
                return job_id
    
    def get_next_job(self) -> Optional[VideoJob]:
        """
        Get the next pending job, atomically claiming it for processing.
        Uses row-level locking to prevent race conditions.
        
        Returns:
            VideoJob if available, None otherwise
        """
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE video_jobs
                    SET status = 'processing', started_at = NOW()
                    WHERE id = (
                        SELECT id FROM video_jobs
                        WHERE status = 'pending'
                        ORDER BY created_at ASC
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING *
                """)
                row = cur.fetchone()
                conn.commit()
                
                if row:
                    return self._row_to_job(row)
                return None
    
    def get_job(self, job_id: int) -> Optional[VideoJob]:
        """Get a specific job by ID."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM video_jobs WHERE id = %s", (job_id,))
                row = cur.fetchone()
                if row:
                    return self._row_to_job(row)
                return None
    
    def get_user_jobs(self, user_id: str, limit: int = 10) -> List[VideoJob]:
        """Get recent jobs for a user."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT * FROM video_jobs
                    WHERE user_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                """, (user_id, limit))
                rows = cur.fetchall()
                return [self._row_to_job(row) for row in rows]
    
    def get_active_jobs(self, user_id: str) -> List[VideoJob]:
        """Get jobs that are currently pending or processing for a user."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT * FROM video_jobs
                    WHERE user_id = %s AND status IN ('pending', 'processing')
                    ORDER BY created_at ASC
                """, (user_id,))
                rows = cur.fetchall()
                return [self._row_to_job(row) for row in rows]
    
    def update_progress(
        self,
        job_id: int,
        current: int,
        total: int,
        message: Optional[str] = None
    ):
        """Update job progress."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE video_jobs
                    SET progress_current = %s, progress_total = %s, progress_message = %s
                    WHERE id = %s
                """, (current, total, message, job_id))
                conn.commit()
    
    def complete_job(self, job_id: int, result_url: str):
        """Mark a job as completed with the result URL."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE video_jobs
                    SET status = 'completed', result_url = %s, completed_at = NOW(),
                        progress_current = progress_total, progress_message = 'Complete!'
                    WHERE id = %s
                """, (result_url, job_id))
                conn.commit()
                print(f"[JobQueue] Job {job_id} completed: {result_url}")
    
    def fail_job(self, job_id: int, error_message: str):
        """Mark a job as failed with an error message."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE video_jobs
                    SET status = 'failed', error_message = %s, completed_at = NOW()
                    WHERE id = %s
                """, (error_message, job_id))
                conn.commit()
                print(f"[JobQueue] Job {job_id} failed: {error_message}")
    
    def cancel_job(self, job_id: int, user_id: str) -> bool:
        """Cancel a pending job (only owner can cancel)."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE video_jobs
                    SET status = 'cancelled', completed_at = NOW()
                    WHERE id = %s AND user_id = %s AND status = 'pending'
                    RETURNING id
                """, (job_id, user_id))
                result = cur.fetchone()
                conn.commit()
                return result is not None
    
    def get_queue_position(self, job_id: int) -> int:
        """Get the position of a job in the queue (1-indexed)."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) + 1 as position
                    FROM video_jobs
                    WHERE status = 'pending'
                    AND created_at < (
                        SELECT created_at FROM video_jobs WHERE id = %s
                    )
                """, (job_id,))
                row = cur.fetchone()
                return row['position'] if row else 0
    
    def get_queue_stats(self) -> Dict[str, int]:
        """Get overall queue statistics."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT status, COUNT(*) as count
                    FROM video_jobs
                    GROUP BY status
                """)
                rows = cur.fetchall()
                stats = {row['status']: row['count'] for row in rows}
                return {
                    'pending': stats.get('pending', 0),
                    'processing': stats.get('processing', 0),
                    'completed': stats.get('completed', 0),
                    'failed': stats.get('failed', 0)
                }
    
    def _row_to_job(self, row: Dict) -> VideoJob:
        """Convert a database row to a VideoJob object."""
        job_data = row.get('job_data')
        if isinstance(job_data, str):
            job_data = json.loads(job_data)
        
        return VideoJob(
            id=row['id'],
            user_id=row['user_id'],
            project_id=row['project_id'],
            status=row['status'],
            quality_tier=row['quality_tier'],
            progress_current=row['progress_current'] or 0,
            progress_total=row['progress_total'] or 0,
            progress_message=row.get('progress_message'),
            result_url=row.get('result_url'),
            error_message=row.get('error_message'),
            job_data=job_data,
            created_at=row['created_at'],
            started_at=row.get('started_at'),
            completed_at=row.get('completed_at')
        )
    
    def to_dict(self, job: VideoJob) -> Dict[str, Any]:
        """Convert a VideoJob to a JSON-serializable dict."""
        return {
            'id': job.id,
            'user_id': job.user_id,
            'project_id': job.project_id,
            'status': job.status,
            'quality_tier': job.quality_tier,
            'progress': {
                'current': job.progress_current,
                'total': job.progress_total,
                'message': job.progress_message
            },
            'result_url': job.result_url,
            'error_message': job.error_message,
            'created_at': job.created_at.isoformat() if job.created_at else None,
            'started_at': job.started_at.isoformat() if job.started_at else None,
            'completed_at': job.completed_at.isoformat() if job.completed_at else None
        }


JOB_QUEUE = JobQueue()
