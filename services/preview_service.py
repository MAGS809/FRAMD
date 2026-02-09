import os
import subprocess
import tempfile
import threading
import traceback
import time
from typing import Optional


PREVIEW_DIR = os.path.join(tempfile.gettempdir(), "framd_previews")
os.makedirs(PREVIEW_DIR, exist_ok=True)


def _get_clip_duration(clip_path: str) -> float:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", clip_path],
            capture_output=True, text=True, timeout=10
        )
        return float(result.stdout.strip())
    except Exception:
        return 5.0


def _extract_segment(source_path: str, start_time: float, duration: float, output_path: str) -> bool:
    try:
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_time),
            "-i", source_path,
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-an",
            output_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and os.path.exists(output_path):
            print(f"[Preview] Extracted segment: {start_time}s +{duration}s -> {output_path}")
            return True
        else:
            print(f"[Preview] Extract failed: {result.stderr[:300]}")
            return False
    except Exception as e:
        print(f"[Preview] Extract error: {e}")
        return False


def _stitch_with_transition(clip1_path: str, clip2_path: str, transition: str, output_path: str) -> bool:
    transition = (transition or "cut").lower()

    try:
        clip1_dur = _get_clip_duration(clip1_path)
        overlap = min(1.0, clip1_dur * 0.2)
        offset = max(0.5, clip1_dur - overlap)

        if transition in ("crossfade", "dissolve", "fade"):
            cmd = [
                "ffmpeg", "-y",
                "-i", clip1_path,
                "-i", clip2_path,
                "-filter_complex",
                f"[0:v]setpts=PTS-STARTPTS[v0];"
                f"[1:v]setpts=PTS-STARTPTS[v1];"
                f"[v0][v1]xfade=transition=fade:duration={overlap:.2f}:offset={offset:.2f}[outv]",
                "-map", "[outv]",
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-an",
                output_path
            ]
        else:
            cmd = [
                "ffmpeg", "-y",
                "-i", clip1_path,
                "-i", clip2_path,
                "-filter_complex",
                "[0:v]setpts=PTS-STARTPTS[v0];"
                "[1:v]setpts=PTS-STARTPTS[v1];"
                "[v0][v1]concat=n=2:v=1:a=0[outv]",
                "-map", "[outv]",
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-an",
                output_path
            ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            print(f"[Preview] Stitched transition preview: {output_path}")
            return True
        else:
            print(f"[Preview] FFmpeg stitch failed: {result.stderr[:300]}")
            return False
    except Exception as e:
        print(f"[Preview] Stitch error: {e}")
        return False


def generate_scene_preview_async(
    project_id: int,
    scene1_plan_id: int,
    scene1_data: dict,
    scene2_data: dict = None,
    scene2_plan_id: int = None,
    quality_tier: str = "good"
):
    thread = threading.Thread(
        target=_run_preview_generation,
        args=(project_id, scene1_plan_id, scene1_data, scene2_data, scene2_plan_id, quality_tier),
        daemon=True
    )
    thread.start()
    return thread


def _find_source_video(project_id: int) -> Optional[str]:
    from models import ProjectSource
    sources = ProjectSource.query.filter_by(project_id=project_id).all()
    for source in sources:
        if source.file_path and os.path.exists(source.file_path):
            return source.file_path
    return None


def _run_preview_generation(
    project_id: int,
    scene1_plan_id: int,
    scene1_data: dict,
    scene2_data: dict,
    scene2_plan_id: int,
    quality_tier: str
):
    from app import app

    with app.app_context():
        from models import db, ScenePlan

        try:
            scene1 = ScenePlan.query.get(scene1_plan_id)
            if not scene1:
                print(f"[Preview] Scene plan {scene1_plan_id} not found")
                return

            scene1.render_status = "generating_preview"
            db.session.commit()

            source_video = _find_source_video(project_id)
            if not source_video:
                scene1.render_status = "preview_failed"
                scene1.source_config = {
                    **(scene1.source_config or {}),
                    "preview_error": "No uploaded source video found for this project"
                }
                db.session.commit()
                print(f"[Preview] No source video found for project {project_id}")
                return

            source_duration = _get_clip_duration(source_video)
            print(f"[Preview] Source video: {source_video} ({source_duration:.1f}s)")

            ts = int(time.time())
            try:
                scene1_duration = float(scene1_data.get("duration", 5.0) or 5.0)
            except (TypeError, ValueError):
                scene1_duration = 5.0
            try:
                scene1_start = float(scene1_data.get("start_time", 0) or 0)
            except (TypeError, ValueError):
                scene1_start = 0.0

            if scene1_start + scene1_duration > source_duration:
                scene1_start = 0
                scene1_duration = min(scene1_duration, source_duration / 2 if scene2_data else source_duration)

            clip1_path = os.path.join(PREVIEW_DIR, f"preview_{project_id}_{ts}_s1.mp4")

            print(f"[Preview] Step 1: Extracting Scene 1 ({scene1_start:.1f}s + {scene1_duration:.1f}s)...")
            scene1.render_status = "extracting_scene1"
            db.session.commit()

            if not _extract_segment(source_video, scene1_start, scene1_duration, clip1_path):
                scene1 = ScenePlan.query.get(scene1_plan_id)
                if scene1:
                    scene1.render_status = "preview_failed"
                    scene1.source_config = {
                        **(scene1.source_config or {}),
                        "preview_error": "Failed to extract Scene 1 from source video"
                    }
                    db.session.commit()
                return

            if not scene2_data:
                scene1 = ScenePlan.query.get(scene1_plan_id)
                if scene1:
                    scene1.rendered_path = clip1_path
                    scene1.render_status = "preview_ready"
                    scene1.source_config = {
                        **(scene1.source_config or {}),
                        "preview_local_path": clip1_path,
                        "preview_video_url": f"/api/project/{project_id}/preview-video",
                        "is_transition_preview": False
                    }
                    db.session.commit()
                    print(f"[Preview] Scene 1 only preview ready (no Scene 2)")
                return

            try:
                scene2_duration = float(scene2_data.get("duration", 5.0) or 5.0)
            except (TypeError, ValueError):
                scene2_duration = 5.0
            scene2_start = scene1_start + scene1_duration

            if scene2_start + scene2_duration > source_duration:
                scene2_start = min(scene2_start, max(0, source_duration - scene2_duration))
                scene2_duration = min(scene2_duration, source_duration - scene2_start)

            if scene2_duration < 1.0:
                scene1 = ScenePlan.query.get(scene1_plan_id)
                if scene1:
                    scene1.rendered_path = clip1_path
                    scene1.render_status = "preview_ready"
                    scene1.source_config = {
                        **(scene1.source_config or {}),
                        "preview_local_path": clip1_path,
                        "preview_video_url": f"/api/project/{project_id}/preview-video",
                        "is_transition_preview": False,
                        "scene2_fallback": True
                    }
                    db.session.commit()
                    print(f"[Preview] Source too short for Scene 2, showing Scene 1 only")
                return

            clip2_path = os.path.join(PREVIEW_DIR, f"preview_{project_id}_{ts}_s2.mp4")
            output_path = os.path.join(PREVIEW_DIR, f"preview_{project_id}_{ts}_transition.mp4")

            print(f"[Preview] Step 2: Extracting Scene 2 ({scene2_start:.1f}s + {scene2_duration:.1f}s)...")
            scene1 = ScenePlan.query.get(scene1_plan_id)
            if scene1:
                scene1.render_status = "extracting_scene2"
                db.session.commit()

            if not _extract_segment(source_video, scene2_start, scene2_duration, clip2_path):
                scene1 = ScenePlan.query.get(scene1_plan_id)
                if scene1:
                    scene1.rendered_path = clip1_path
                    scene1.render_status = "preview_ready"
                    scene1.source_config = {
                        **(scene1.source_config or {}),
                        "preview_local_path": clip1_path,
                        "preview_video_url": f"/api/project/{project_id}/preview-video",
                        "is_transition_preview": False,
                        "scene2_fallback": True
                    }
                    db.session.commit()
                    print(f"[Preview] Scene 2 extract failed, showing Scene 1 only")
                return

            print(f"[Preview] Step 3: Stitching Scene 1 → 2 with transition...")
            scene1 = ScenePlan.query.get(scene1_plan_id)
            if scene1:
                scene1.render_status = "stitching_transition"
                db.session.commit()

            transition_type = scene1_data.get("transition_out", "cut")
            stitched = _stitch_with_transition(clip1_path, clip2_path, transition_type, output_path)

            if stitched and os.path.exists(output_path):
                scene1 = ScenePlan.query.get(scene1_plan_id)
                if scene1:
                    scene1.rendered_path = output_path
                    scene1.render_status = "preview_ready"
                    scene1.source_config = {
                        **(scene1.source_config or {}),
                        "preview_video_url": f"/api/project/{project_id}/preview-video",
                        "preview_local_path": output_path,
                        "transition_type": transition_type,
                        "is_transition_preview": True
                    }
                    db.session.commit()
                    print(f"[Preview] Scene 1 → 2 transition preview ready!")
                for f in [clip1_path, clip2_path]:
                    try:
                        os.remove(f)
                    except OSError:
                        pass
                return

            scene1 = ScenePlan.query.get(scene1_plan_id)
            if scene1:
                scene1.rendered_path = clip1_path
                scene1.render_status = "preview_ready"
                scene1.source_config = {
                    **(scene1.source_config or {}),
                    "preview_local_path": clip1_path,
                    "preview_video_url": f"/api/project/{project_id}/preview-video",
                    "is_transition_preview": False,
                    "scene2_fallback": True
                }
                db.session.commit()
                print(f"[Preview] Stitch failed, showing Scene 1 only")

            for f in [clip2_path, output_path]:
                try:
                    os.remove(f)
                except OSError:
                    pass

        except Exception as e:
            print(f"[Preview] Error generating preview: {e}")
            traceback.print_exc()
            try:
                scene1 = ScenePlan.query.get(scene1_plan_id)
                if scene1:
                    scene1.render_status = "preview_failed"
                    scene1.source_config = {
                        **(scene1.source_config or {}),
                        "preview_error": str(e)
                    }
                    db.session.commit()
            except Exception:
                db.session.rollback()
