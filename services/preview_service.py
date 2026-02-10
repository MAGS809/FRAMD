import os
import subprocess
import tempfile
import threading
import traceback
import time
import base64
import requests
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


def _extract_frame(source_path: str, timestamp: float, output_path: str) -> bool:
    try:
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(timestamp),
            "-i", source_path,
            "-frames:v", "1",
            "-q:v", "2",
            output_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0 and os.path.exists(output_path):
            print(f"[Preview] Extracted frame at {timestamp}s -> {output_path}")
            return True
        else:
            print(f"[Preview] Frame extract failed: {result.stderr[:300]}")
            return False
    except Exception as e:
        print(f"[Preview] Frame extract error: {e}")
        return False


def _frame_to_data_uri(frame_path: str) -> Optional[str]:
    try:
        with open(frame_path, "rb") as f:
            data = f.read()
        encoded = base64.b64encode(data).decode("utf-8")
        ext = os.path.splitext(frame_path)[1].lower()
        mime = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"
        return f"data:{mime};base64,{encoded}"
    except Exception as e:
        print(f"[Preview] Data URI encode error: {e}")
        return None


def _download_video(url: str, output_path: str) -> bool:
    try:
        resp = requests.get(url, timeout=60, stream=True)
        if resp.status_code == 200:
            with open(output_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            print(f"[Preview] Downloaded Runway output -> {output_path}")
            return True
        else:
            print(f"[Preview] Download failed: HTTP {resp.status_code}")
            return False
    except Exception as e:
        print(f"[Preview] Download error: {e}")
        return False


def _generate_remix_clip(source_path: str, start_time: float, visual_description: str,
                         quality_tier: str, output_path: str, db_update_fn=None) -> dict:
    """Returns dict with 'success': bool, 'error': optional error message."""
    from remix_engine import (
        runway_generate_video, QualityTier
    )

    ts = int(time.time())
    frame_path = os.path.join(PREVIEW_DIR, f"frame_{ts}.jpg")

    if db_update_fn:
        db_update_fn("extracting_frame")

    if not _extract_frame(source_path, start_time, frame_path):
        return {"success": False, "error": "Failed to extract frame from source video"}

    data_uri = _frame_to_data_uri(frame_path)
    try:
        os.remove(frame_path)
    except OSError:
        pass

    if not data_uri:
        return {"success": False, "error": "Failed to encode frame for Runway"}

    tier_map = {
        "good": QualityTier.GOOD,
        "better": QualityTier.BETTER,
        "best": QualityTier.BEST,
    }
    tier = tier_map.get(quality_tier, QualityTier.GOOD)

    prompt = visual_description or "Cinematic motion, smooth camera movement"
    if len(prompt) > 1000:
        prompt = prompt[:997] + "..."

    if db_update_fn:
        db_update_fn("sending_to_runway")

    print(f"[Preview] Sending frame to Runway: prompt='{prompt[:80]}...', tier={quality_tier}")

    try:
        result = runway_generate_video(
            prompt_image=data_uri,
            prompt_text=prompt,
            quality_tier=tier,
            duration=5,
            ratio="9:16",
            wait_for_completion=False
        )
    except Exception as e:
        print(f"[Preview] Runway submit exception: {e}")
        return {"success": False, "error": f"Runway submission error: {str(e)}"}

    if not result.get("success") or not result.get("task_id"):
        err = result.get('error', 'Unknown error')
        print(f"[Preview] Runway submit failed: {err}")
        return {"success": False, "error": f"Runway rejected request: {err}"}

    task_id = result["task_id"]
    print(f"[Preview] Runway task created: {task_id}")

    if db_update_fn:
        db_update_fn("generating_ai_visuals")

    from remix_engine import runway_wait_for_completion, RunwayError
    try:
        final = runway_wait_for_completion(task_id, max_wait_seconds=300, poll_interval=5)
    except RunwayError as e:
        print(f"[Preview] Runway generation failed: {e.message}")
        return {"success": False, "error": f"Runway generation failed: {e.message}"}

    output_urls = final.output_urls
    if not output_urls or len(output_urls) == 0:
        print("[Preview] Runway returned no output URLs")
        return {"success": False, "error": "Runway returned no output"}

    video_url = output_urls[0]
    print(f"[Preview] Runway output ready: {video_url}")

    if db_update_fn:
        db_update_fn("downloading_result")

    if _download_video(video_url, output_path):
        return {"success": True}
    return {"success": False, "error": "Failed to download Runway output"}


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


def _find_source_video(project_id: int) -> Optional[str]:
    from models import ProjectSource
    sources = ProjectSource.query.filter_by(project_id=project_id).all()
    for source in sources:
        if source.file_path and os.path.exists(source.file_path):
            return source.file_path
    return None


def _is_remix_mode(scene_data: dict) -> bool:
    return (scene_data or {}).get("source_type", "").lower() == "remix"


def _generate_stock_clip(visual_description: str, duration: float, output_path: str, db_update_fn=None) -> dict:
    from remix_engine import search_pexels_videos

    try:
        if db_update_fn:
            db_update_fn("searching_stock")

        query = visual_description or "cinematic background"
        if len(query) > 200:
            query = query[:200]

        print(f"[Preview] Searching Pexels for stock video: '{query[:80]}...'")
        videos = search_pexels_videos(query=query, per_page=3, orientation="portrait")

        if not videos:
            return {"success": False, "error": f"No stock videos found for: {query[:100]}"}

        best_video = videos[0]
        video_url = best_video.get("video_url") or best_video.get("url")
        if not video_url:
            return {"success": False, "error": "Stock video result has no download URL"}

        if db_update_fn:
            db_update_fn("downloading_stock")

        print(f"[Preview] Downloading stock video: {video_url[:100]}...")
        ts = int(time.time())
        raw_path = os.path.join(PREVIEW_DIR, f"stock_raw_{ts}.mp4")

        if not _download_video(video_url, raw_path):
            return {"success": False, "error": "Failed to download stock video"}

        raw_duration = _get_clip_duration(raw_path)
        if raw_duration > duration + 0.5:
            print(f"[Preview] Trimming stock video from {raw_duration:.1f}s to {duration:.1f}s")
            cmd = [
                "ffmpeg", "-y",
                "-i", raw_path,
                "-t", str(duration),
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-an",
                output_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            try:
                os.remove(raw_path)
            except OSError:
                pass
            if result.returncode == 0 and os.path.exists(output_path):
                print(f"[Preview] Stock clip trimmed -> {output_path}")
                return {"success": True}
            else:
                print(f"[Preview] Stock trim failed: {result.stderr[:300]}")
                return {"success": False, "error": "Failed to trim stock video"}
        else:
            os.rename(raw_path, output_path)
            print(f"[Preview] Stock clip ready -> {output_path}")
            return {"success": True}

    except Exception as e:
        print(f"[Preview] Stock clip error: {e}")
        traceback.print_exc()
        return {"success": False, "error": f"Stock clip generation failed: {str(e)}"}


def _generate_dalle_clip(visual_description: str, duration: float, output_path: str, db_update_fn=None) -> dict:
    try:
        if db_update_fn:
            db_update_fn("generating_dalle_image")

        prompt = visual_description or "Abstract cinematic visual, dramatic lighting"
        if len(prompt) > 4000:
            prompt = prompt[:3997] + "..."

        print(f"[Preview] Generating DALL-E image: '{prompt[:80]}...'")

        from openai import OpenAI
        client = OpenAI()
        response = client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1792",
            quality="standard",
            n=1
        )

        image_url = response.data[0].url
        if not image_url:
            return {"success": False, "error": "DALL-E returned no image URL"}

        ts = int(time.time())
        image_path = os.path.join(PREVIEW_DIR, f"dalle_img_{ts}.png")

        print(f"[Preview] Downloading DALL-E image...")
        img_resp = requests.get(image_url, timeout=60)
        if img_resp.status_code != 200:
            return {"success": False, "error": f"Failed to download DALL-E image: HTTP {img_resp.status_code}"}

        with open(image_path, "wb") as f:
            f.write(img_resp.content)

        if db_update_fn:
            db_update_fn("converting_to_video")

        print(f"[Preview] Converting DALL-E image to {duration}s video...")
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", image_path,
            "-c:v", "libx264",
            "-t", str(duration),
            "-pix_fmt", "yuv420p",
            "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
            output_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        try:
            os.remove(image_path)
        except OSError:
            pass

        if result.returncode == 0 and os.path.exists(output_path):
            print(f"[Preview] DALL-E clip ready -> {output_path}")
            return {"success": True}
        else:
            print(f"[Preview] DALL-E video conversion failed: {result.stderr[:300]}")
            return {"success": False, "error": "Failed to convert DALL-E image to video"}

    except Exception as e:
        print(f"[Preview] DALL-E clip error: {e}")
        traceback.print_exc()
        return {"success": False, "error": f"DALL-E clip generation failed: {str(e)}"}


def _render_single_scene(scene_data: dict, scene_plan_id: int, project_id: int,
                         source_video: Optional[str], quality_tier: str) -> dict:
    from models import db, ScenePlan

    source_type = (scene_data.get("source_type") or "clip").lower()
    visual_description = scene_data.get("visual_description", "")

    try:
        duration = float(scene_data.get("duration", 5.0) or 5.0)
    except (TypeError, ValueError):
        duration = 5.0

    try:
        start_time = float(scene_data.get("start_time", 0) or 0)
    except (TypeError, ValueError):
        start_time = 0.0

    output_path = os.path.join(PREVIEW_DIR, f"scene_{project_id}_{scene_plan_id}_{int(time.time())}.mp4")

    def db_update_fn(status):
        try:
            s = ScenePlan.query.get(scene_plan_id)
            if s:
                s.render_status = status
                db.session.commit()
        except Exception:
            db.session.rollback()

    print(f"[Preview] Rendering scene {scene_data.get('scene_index', '?')} type={source_type} duration={duration}s")

    render_result = None

    if source_type == "clip":
        if not source_video:
            render_result = {"success": False, "error": "No source video available for clip extraction"}
        else:
            if start_time + duration > _get_clip_duration(source_video):
                start_time = 0
                duration = min(duration, _get_clip_duration(source_video))
            db_update_fn("extracting_clip")
            success = _extract_segment(source_video, start_time, duration, output_path)
            render_result = {"success": success} if success else {"success": False, "error": "Failed to extract clip segment"}

    elif source_type == "remix":
        if not source_video:
            render_result = {"success": False, "error": "No source video available for remix"}
        else:
            render_result = _generate_remix_clip(
                source_path=source_video,
                start_time=start_time,
                visual_description=visual_description,
                quality_tier=quality_tier,
                output_path=output_path,
                db_update_fn=db_update_fn
            )

    elif source_type == "stock":
        render_result = _generate_stock_clip(
            visual_description=visual_description,
            duration=duration,
            output_path=output_path,
            db_update_fn=db_update_fn
        )

    elif source_type == "dalle":
        render_result = _generate_dalle_clip(
            visual_description=visual_description,
            duration=duration,
            output_path=output_path,
            db_update_fn=db_update_fn
        )

    else:
        render_result = {"success": False, "error": f"Unknown source type: {source_type}"}

    try:
        scene_plan = ScenePlan.query.get(scene_plan_id)
        if scene_plan:
            if render_result.get("success"):
                scene_plan.rendered_path = output_path
                scene_plan.render_status = "rendered"
                scene_plan.source_config = {
                    **(scene_plan.source_config or {}),
                    "preview_local_path": output_path,
                    "preview_video_url": f"/api/project/{project_id}/scene/{scene_plan_id}/preview-video"
                }
                print(f"[Preview] Scene {scene_data.get('scene_index', '?')} rendered successfully")
            else:
                scene_plan.render_status = "render_failed"
                scene_plan.source_config = {
                    **(scene_plan.source_config or {}),
                    "render_error": render_result.get("error", "Unknown error")
                }
                print(f"[Preview] Scene {scene_data.get('scene_index', '?')} render failed: {render_result.get('error')}")
            db.session.commit()
    except Exception as e:
        print(f"[Preview] DB update error for scene {scene_plan_id}: {e}")
        db.session.rollback()

    return {
        "success": render_result.get("success", False),
        "output_path": output_path if render_result.get("success") else None,
        "error": render_result.get("error")
    }


def generate_all_scenes_async(project_id: int, scene_plan_data: list, quality_tier: str = "good"):
    thread = threading.Thread(
        target=_run_all_scenes_generation,
        args=(project_id, scene_plan_data, quality_tier),
        daemon=True
    )
    thread.start()
    return thread


def _run_all_scenes_generation(project_id: int, scene_plan_data: list, quality_tier: str):
    from app import app

    with app.app_context():
        from models import db, ScenePlan

        try:
            scene_plans = ScenePlan.query.filter_by(project_id=project_id).order_by(ScenePlan.scene_index).all()
            if not scene_plans:
                print(f"[Preview] No scene plans found for project {project_id}")
                return

            source_video = _find_source_video(project_id)
            print(f"[Preview] Starting all-scenes render for project {project_id}: {len(scene_plans)} scenes, source_video={'found' if source_video else 'None'}")

            rendered_paths = []
            total_scenes = len(scene_plans)

            for i, scene_plan in enumerate(scene_plans):
                try:
                    first_sp = ScenePlan.query.get(scene_plans[0].id)
                    if first_sp:
                        first_sp.source_config = {
                            **(first_sp.source_config or {}),
                            "all_scenes_status": "rendering",
                            "current_scene": i + 1,
                            "total_scenes": total_scenes
                        }
                        db.session.commit()
                except Exception:
                    db.session.rollback()

                matching_data = None
                for sd in scene_plan_data:
                    if sd.get("scene_index") == scene_plan.scene_index:
                        matching_data = sd
                        break

                if not matching_data:
                    if i < len(scene_plan_data):
                        matching_data = scene_plan_data[i]
                    else:
                        matching_data = {
                            "scene_index": scene_plan.scene_index,
                            "source_type": scene_plan.source_type or "clip",
                            "visual_description": (scene_plan.source_config or {}).get("visual_description", ""),
                            "duration": scene_plan.duration or 5.0,
                            "start_time": scene_plan.start_time or 0,
                            "transition_out": scene_plan.transition_out or "cut"
                        }

                result = _render_single_scene(
                    scene_data=matching_data,
                    scene_plan_id=scene_plan.id,
                    project_id=project_id,
                    source_video=source_video,
                    quality_tier=quality_tier
                )

                if not result.get("success") and matching_data.get("source_type", "").lower() in ("clip", "remix") and source_video:
                    print(f"[Preview] Scene {i+1} failed ({matching_data.get('source_type')}), falling back to clip extraction")
                    try:
                        start_time = float(matching_data.get("start_time", 0) or 0)
                    except (TypeError, ValueError):
                        start_time = 0.0
                    try:
                        duration = float(matching_data.get("duration", 5.0) or 5.0)
                    except (TypeError, ValueError):
                        duration = 5.0

                    fallback_path = os.path.join(PREVIEW_DIR, f"scene_fallback_{project_id}_{scene_plan.id}_{int(time.time())}.mp4")
                    if _extract_segment(source_video, start_time, duration, fallback_path):
                        try:
                            sp = ScenePlan.query.get(scene_plan.id)
                            if sp:
                                sp.rendered_path = fallback_path
                                sp.render_status = "rendered"
                                sp.source_config = {
                                    **(sp.source_config or {}),
                                    "preview_local_path": fallback_path,
                                    "preview_video_url": f"/api/project/{project_id}/scene/{scene_plan.id}/preview-video",
                                    "fallback_used": True
                                }
                                db.session.commit()
                        except Exception:
                            db.session.rollback()
                        result = {"success": True, "output_path": fallback_path}

                rendered_paths.append(result)

            successful_paths = [r["output_path"] for r in rendered_paths if r.get("success") and r.get("output_path")]

            if len(successful_paths) >= 2:
                ts = int(time.time())
                transition_output = os.path.join(PREVIEW_DIR, f"preview_{project_id}_{ts}_transition.mp4")

                scene1_data_for_transition = scene_plan_data[0] if scene_plan_data else {}
                transition_type = scene1_data_for_transition.get("transition_out", "cut")

                print(f"[Preview] Stitching Scene 1→2 transition preview...")
                stitched = _stitch_with_transition(
                    successful_paths[0],
                    successful_paths[1],
                    transition_type,
                    transition_output
                )

                if stitched and os.path.exists(transition_output):
                    try:
                        first_sp = ScenePlan.query.get(scene_plans[0].id)
                        if first_sp:
                            first_sp.rendered_path = transition_output
                            first_sp.source_config = {
                                **(first_sp.source_config or {}),
                                "preview_local_path": transition_output,
                                "preview_video_url": f"/api/project/{project_id}/preview-video",
                                "is_transition_preview": True
                            }
                            db.session.commit()
                        print(f"[Preview] Scene 1→2 transition preview stitched")
                    except Exception:
                        db.session.rollback()

            try:
                first_sp = ScenePlan.query.get(scene_plans[0].id)
                if first_sp:
                    first_sp.source_config = {
                        **(first_sp.source_config or {}),
                        "all_scenes_status": "ready",
                        "all_scenes_rendered": True,
                        "preview_video_url": f"/api/project/{project_id}/preview-video"
                    }
                    first_sp.render_status = "preview_ready"
                    db.session.commit()
            except Exception:
                db.session.rollback()

            success_count = sum(1 for r in rendered_paths if r.get("success"))
            print(f"[Preview] All scenes complete: {success_count}/{total_scenes} rendered successfully")

        except Exception as e:
            print(f"[Preview] Error in all-scenes generation: {e}")
            traceback.print_exc()
            try:
                first_sp = ScenePlan.query.filter_by(project_id=project_id).order_by(ScenePlan.scene_index).first()
                if first_sp:
                    first_sp.render_status = "preview_failed"
                    first_sp.source_config = {
                        **(first_sp.source_config or {}),
                        "all_scenes_status": "failed",
                        "preview_error": str(e)
                    }
                    db.session.commit()
            except Exception:
                db.session.rollback()


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


generate_scene_preview_async_legacy = generate_scene_preview_async


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

            is_remix = _is_remix_mode(scene1_data)
            print(f"[Preview] Mode: {'REMIX' if is_remix else 'CLIP'}")

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

            def update_scene1_status(status):
                try:
                    s = ScenePlan.query.get(scene1_plan_id)
                    if s:
                        s.render_status = status
                        db.session.commit()
                except Exception:
                    db.session.rollback()

            if is_remix:
                visual_desc = scene1_data.get("visual_description", "")
                print(f"[Preview] REMIX Scene 1: extracting frame at {scene1_start}s, sending to Runway...")

                remix_result = _generate_remix_clip(
                    source_path=source_video,
                    start_time=scene1_start,
                    visual_description=visual_desc,
                    quality_tier=quality_tier,
                    output_path=clip1_path,
                    db_update_fn=update_scene1_status
                )

                if not remix_result.get("success"):
                    remix_error = remix_result.get("error", "Unknown remix error")
                    print(f"[Preview] Remix Scene 1 failed: {remix_error}")
                    print("[Preview] Falling back to clip extraction")
                    update_scene1_status("extracting_scene1")
                    if not _extract_segment(source_video, scene1_start, scene1_duration, clip1_path):
                        update_scene1_status("preview_failed")
                        scene1 = ScenePlan.query.get(scene1_plan_id)
                        if scene1:
                            scene1.source_config = {
                                **(scene1.source_config or {}),
                                "preview_error": f"Remix failed: {remix_error}. Clip fallback also failed."
                            }
                            db.session.commit()
                        return
                    is_remix = False
            else:
                print(f"[Preview] Step 1: Extracting Scene 1 ({scene1_start:.1f}s + {scene1_duration:.1f}s)...")
                update_scene1_status("extracting_scene1")

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
                        "is_transition_preview": False,
                        "is_remix": is_remix
                    }
                    db.session.commit()
                    mode_label = "Remix" if is_remix else "Clip"
                    print(f"[Preview] Scene 1 only ({mode_label}) preview ready")
                return

            is_remix_s2 = _is_remix_mode(scene2_data)

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
                        "scene2_fallback": True,
                        "is_remix": is_remix
                    }
                    db.session.commit()
                    print(f"[Preview] Source too short for Scene 2, showing Scene 1 only")
                return

            clip2_path = os.path.join(PREVIEW_DIR, f"preview_{project_id}_{ts}_s2.mp4")
            output_path = os.path.join(PREVIEW_DIR, f"preview_{project_id}_{ts}_transition.mp4")

            if is_remix_s2:
                visual_desc_s2 = scene2_data.get("visual_description", "")
                print(f"[Preview] REMIX Scene 2: extracting frame at {scene2_start}s, sending to Runway...")
                update_scene1_status("generating_scene2_ai")

                remix_s2_result = _generate_remix_clip(
                    source_path=source_video,
                    start_time=scene2_start,
                    visual_description=visual_desc_s2,
                    quality_tier=quality_tier,
                    output_path=clip2_path,
                    db_update_fn=update_scene1_status
                )

                if not remix_s2_result.get("success"):
                    remix_s2_error = remix_s2_result.get("error", "Unknown")
                    print(f"[Preview] Remix Scene 2 failed: {remix_s2_error}, falling back to clip")
                    update_scene1_status("extracting_scene2")
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
                                "scene2_fallback": True,
                                "is_remix": is_remix
                            }
                            db.session.commit()
                            print(f"[Preview] Scene 2 extract also failed, showing Scene 1 only")
                        return
            else:
                print(f"[Preview] Step 2: Extracting Scene 2 ({scene2_start:.1f}s + {scene2_duration:.1f}s)...")
                update_scene1_status("extracting_scene2")

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
            update_scene1_status("stitching_transition")

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
                        "is_transition_preview": True,
                        "is_remix": is_remix or is_remix_s2
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


_run_preview_generation_legacy = _run_preview_generation
