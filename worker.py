"""
Background Worker for Video Generation Jobs

This worker polls the job queue and processes video generation requests.
It runs independently from the main web server and can be scaled by
running multiple worker instances.

Usage:
    python worker.py

For production, run this as a separate workflow/process.
"""

import os
import sys
import time
import signal
import traceback
from typing import Optional

from job_queue import JOB_QUEUE, VideoJob, JobStatus
from remix_engine import (
    QualityTier,
    RUNWAY_QUEUE,
    create_orchestration_plan,
    execute_orchestration,
    VibeProfile
)


POLL_INTERVAL = 2.0
SHUTDOWN_REQUESTED = False


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global SHUTDOWN_REQUESTED
    print("\n[Worker] Shutdown requested, finishing current job...")
    SHUTDOWN_REQUESTED = True


def get_quality_tier(tier_str: str) -> QualityTier:
    """Convert string to QualityTier enum."""
    tier_map = {
        'good': QualityTier.GOOD,
        'better': QualityTier.BETTER,
        'best': QualityTier.BEST
    }
    return tier_map.get(tier_str.lower(), QualityTier.GOOD)


def process_job(job: VideoJob) -> bool:
    """
    Process a single video generation job.
    
    Returns:
        True if successful, False if failed
    """
    print(f"[Worker] Processing job {job.id} (project={job.project_id}, quality={job.quality_tier})")
    
    try:
        job_data = job.job_data or {}
        quality_tier = get_quality_tier(job.quality_tier)
        
        vibe_profile = VibeProfile(
            mood=job_data.get('mood', 'inspirational'),
            energy_level=job_data.get('energy_level', 0.6),
            pacing=job_data.get('pacing', 'steady'),
            visual_style=job_data.get('visual_style', 'cinematic'),
            color_palette=job_data.get('color_palette', ['#1a1a2e', '#16213e', '#0f3460', '#e94560']),
            cut_rhythm=job_data.get('cut_rhythm', 'flowing'),
            reference_description=job_data.get('reference_description', '')
        )
        
        source_images = job_data.get('source_images', [])
        content_files = job_data.get('content_files', [])
        runway_instructions = job_data.get('runway_instructions', [])
        
        total_scenes = len(runway_instructions) if runway_instructions else 1
        JOB_QUEUE.update_progress(job.id, 0, total_scenes, "Starting generation...")
        
        def progress_callback(current, total, message):
            JOB_QUEUE.update_progress(job.id, current, total, message)
        
        if runway_instructions:
            from remix_engine import RunwayInstruction
            
            class MockPlan:
                def __init__(self):
                    self.vibe_profile = vibe_profile
                    self.runway_instructions = [
                        RunwayInstruction(
                            scene_id=instr.get('scene_id', f'scene_{i}'),
                            prompt=instr.get('prompt', ''),
                            duration=instr.get('duration', 5),
                            generation_type=instr.get('generation_type', 'image_to_video'),
                            style_modifiers=instr.get('style_modifiers', []),
                            camera_motion=instr.get('camera_motion'),
                            reference_assets=instr.get('reference_assets', [])
                        )
                        for i, instr in enumerate(runway_instructions)
                    ]
                    self.stock_queries = job_data.get('stock_queries', [])
                    self.total_duration = sum(instr.get('duration', 5) for instr in runway_instructions)
                    self.estimated_cost = job_data.get('estimated_cost', 0)
            
            plan = MockPlan()
        else:
            JOB_QUEUE.update_progress(job.id, 0, 1, "No generation instructions found")
            JOB_QUEUE.fail_job(job.id, "No video instructions provided")
            return False
        
        result = execute_orchestration(
            plan=plan,
            quality_tier=quality_tier,
            source_images=source_images,
            content_files=content_files,
            wait_for_completion=True
        )
        
        if result.get('final_video_url'):
            JOB_QUEUE.complete_job(job.id, result['final_video_url'])
            print(f"[Worker] Job {job.id} completed successfully: {result['final_video_url']}")
            return True
        elif result.get('errors'):
            error_msg = result['errors'][0] if result['errors'] else "Unknown error"
            user_msg = result.get('user_message', error_msg)
            JOB_QUEUE.fail_job(job.id, user_msg)
            print(f"[Worker] Job {job.id} failed: {user_msg}")
            return False
        else:
            JOB_QUEUE.update_progress(job.id, total_scenes, total_scenes, "Processing complete, awaiting final assembly...")
            
            if result.get('shotstack_result', {}).get('render_id'):
                JOB_QUEUE.complete_job(job.id, f"pending:{result['shotstack_result']['render_id']}")
            else:
                JOB_QUEUE.fail_job(job.id, "Video assembly did not complete")
            return False
            
    except Exception as e:
        error_msg = f"Processing error: {str(e)}"
        print(f"[Worker] Job {job.id} exception: {error_msg}")
        traceback.print_exc()
        JOB_QUEUE.fail_job(job.id, "Something went wrong. Please try again.")
        return False


def run_worker():
    """
    Main worker loop. Polls for jobs and processes them.
    """
    print("[Worker] Starting video generation worker...")
    print(f"[Worker] Poll interval: {POLL_INTERVAL}s")
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    jobs_processed = 0
    
    while not SHUTDOWN_REQUESTED:
        try:
            job = JOB_QUEUE.get_next_job()
            
            if job:
                success = process_job(job)
                jobs_processed += 1
                print(f"[Worker] Total jobs processed: {jobs_processed}")
            else:
                time.sleep(POLL_INTERVAL)
                
        except KeyboardInterrupt:
            print("\n[Worker] Interrupted")
            break
        except Exception as e:
            print(f"[Worker] Error in main loop: {e}")
            traceback.print_exc()
            time.sleep(POLL_INTERVAL)
    
    print(f"[Worker] Shutting down. Total jobs processed: {jobs_processed}")


if __name__ == "__main__":
    run_worker()
