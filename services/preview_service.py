import os
import subprocess
import tempfile
import threading
import traceback
import urllib.request
from typing import Optional

from openai import OpenAI

openai_client = OpenAI(
    api_key=os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY"),
    base_url=os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")
)

RUNWAY_API_KEY = os.environ.get("RUNWAY_API_KEY")

PREVIEW_DIR = os.path.join(tempfile.gettempdir(), "framd_previews")
os.makedirs(PREVIEW_DIR, exist_ok=True)


def generate_dalle_image(visual_description: str, script_text: str, aspect_ratio: str = "9:16") -> Optional[str]:
    prompt = f"Cinematic video frame. {visual_description}. Scene context: {script_text}. Photorealistic, cinematic lighting, film grain, shallow depth of field. No text overlays."

    size_map = {
        "9:16": "1024x1792",
        "16:9": "1792x1024",
        "1:1": "1024x1024",
        "4:5": "1024x1024",
    }
    size = size_map.get(aspect_ratio, "1024x1792")

    try:
        response = openai_client.images.generate(
            model="dall-e-3",
            prompt=prompt[:4000],
            size=size,
            quality="standard",
            n=1,
        )
        image_url = response.data[0].url
        print(f"[Preview] DALL-E image generated: {image_url[:80]}...")
        return image_url
    except Exception as e:
        print(f"[Preview] DALL-E generation failed: {e}")
        return None


def generate_runway_preview(
    image_url: str,
    motion_prompt: str,
    quality_tier: str = "good",
    duration: int = 5,
    ratio: str = "9:16"
) -> Optional[str]:
    from remix_engine import (
        QualityTier,
        runway_generate_with_retry
    )

    tier_map = {
        "good": QualityTier.GOOD,
        "better": QualityTier.BETTER,
        "best": QualityTier.BEST,
    }
    tier = tier_map.get(quality_tier, QualityTier.GOOD)

    result = runway_generate_with_retry(
        prompt_image=image_url,
        prompt_text=motion_prompt[:1000],
        quality_tier=tier,
        duration=duration,
        ratio=ratio,
        max_retries=2,
        base_delay=3.0,
    )

    if result.get("success") and result.get("output_url"):
        print(f"[Preview] Runway video generated: {result['output_url'][:80]}...")
        return result["output_url"]
    else:
        print(f"[Preview] Runway generation failed: {result.get('error', 'Unknown')}")
        return None


def _build_motion_prompt(visual_desc: str, container: str) -> str:
    motion_prompt = f"Cinematic motion. {visual_desc}. Slow, deliberate camera movement. Film-quality."
    if container == "split_screen":
        motion_prompt += " Split screen composition with two perspectives."
    elif container == "card":
        motion_prompt += " Content framed within a floating card element."
    elif container == "frame":
        motion_prompt += " Content within a cinematic frame border."
    return motion_prompt


def _download_video(url: str, dest_path: str) -> bool:
    try:
        urllib.request.urlretrieve(url, dest_path)
        return True
    except Exception as e:
        print(f"[Preview] Download failed: {e}")
        return False


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

            visual_desc_1 = scene1_data.get("visual_description", "")
            script_text_1 = scene1_data.get("script_text", "")
            if not visual_desc_1:
                visual_desc_1 = f"A cinematic scene depicting: {script_text_1}"

            print(f"[Preview] Step 1/4: Generating DALL-E base image for Scene 1...")
            dalle_url_1 = generate_dalle_image(visual_desc_1, script_text_1)

            if not dalle_url_1:
                scene1.render_status = "preview_failed"
                scene1.source_config = {
                    **(scene1.source_config or {}),
                    "preview_error": "Failed to generate Scene 1 base image"
                }
                db.session.commit()
                return

            scene1.source_config = {
                **(scene1.source_config or {}),
                "dalle_preview_url": dalle_url_1
            }
            scene1.render_status = "generating_runway"
            db.session.commit()

            motion_prompt_1 = _build_motion_prompt(visual_desc_1, scene1_data.get("visual_container", "fullscreen"))

            print(f"[Preview] Step 2/4: Generating Runway video for Scene 1...")
            video_url_1 = generate_runway_preview(
                image_url=dalle_url_1,
                motion_prompt=motion_prompt_1,
                quality_tier=quality_tier,
                duration=5,
                ratio="9:16"
            )

            if not video_url_1:
                scene1 = ScenePlan.query.get(scene1_plan_id)
                if scene1:
                    scene1.render_status = "preview_failed"
                    scene1.source_config = {
                        **(scene1.source_config or {}),
                        "preview_error": "Scene 1 Runway generation failed",
                        "dalle_preview_url": dalle_url_1
                    }
                    db.session.commit()
                return

            if not scene2_data:
                scene1 = ScenePlan.query.get(scene1_plan_id)
                if scene1:
                    scene1.rendered_path = video_url_1
                    scene1.render_status = "preview_ready"
                    scene1.source_config = {
                        **(scene1.source_config or {}),
                        "preview_video_url": video_url_1,
                        "dalle_preview_url": dalle_url_1
                    }
                    db.session.commit()
                    print(f"[Preview] Scene 1 only preview ready (no Scene 2 available)")
                return

            scene1 = ScenePlan.query.get(scene1_plan_id)
            if scene1:
                scene1.render_status = "generating_scene2"
                db.session.commit()

            visual_desc_2 = scene2_data.get("visual_description", "")
            script_text_2 = scene2_data.get("script_text", "")
            if not visual_desc_2:
                visual_desc_2 = f"A cinematic scene depicting: {script_text_2}"

            print(f"[Preview] Step 3/4: Generating DALL-E base image for Scene 2...")
            dalle_url_2 = generate_dalle_image(visual_desc_2, script_text_2)

            if not dalle_url_2:
                scene1 = ScenePlan.query.get(scene1_plan_id)
                if scene1:
                    scene1.rendered_path = video_url_1
                    scene1.render_status = "preview_ready"
                    scene1.source_config = {
                        **(scene1.source_config or {}),
                        "preview_video_url": video_url_1,
                        "dalle_preview_url": dalle_url_1,
                        "scene2_fallback": True
                    }
                    db.session.commit()
                    print(f"[Preview] Scene 2 DALL-E failed, falling back to Scene 1 only")
                return

            motion_prompt_2 = _build_motion_prompt(visual_desc_2, scene2_data.get("visual_container", "fullscreen"))

            print(f"[Preview] Step 4/4: Generating Runway video for Scene 2...")
            video_url_2 = generate_runway_preview(
                image_url=dalle_url_2,
                motion_prompt=motion_prompt_2,
                quality_tier=quality_tier,
                duration=5,
                ratio="9:16"
            )

            if not video_url_2:
                scene1 = ScenePlan.query.get(scene1_plan_id)
                if scene1:
                    scene1.rendered_path = video_url_1
                    scene1.render_status = "preview_ready"
                    scene1.source_config = {
                        **(scene1.source_config or {}),
                        "preview_video_url": video_url_1,
                        "dalle_preview_url": dalle_url_1,
                        "scene2_fallback": True
                    }
                    db.session.commit()
                    print(f"[Preview] Scene 2 Runway failed, falling back to Scene 1 only")
                return

            scene1 = ScenePlan.query.get(scene1_plan_id)
            if scene1:
                scene1.render_status = "stitching_transition"
                db.session.commit()

            import time
            ts = int(time.time())
            print(f"[Preview] Stitching Scene 1 → 2 with transition...")
            clip1_path = os.path.join(PREVIEW_DIR, f"preview_{project_id}_{ts}_s1.mp4")
            clip2_path = os.path.join(PREVIEW_DIR, f"preview_{project_id}_{ts}_s2.mp4")
            output_path = os.path.join(PREVIEW_DIR, f"preview_{project_id}_{ts}_transition.mp4")

            dl1 = _download_video(video_url_1, clip1_path)
            dl2 = _download_video(video_url_2, clip2_path)

            if dl1 and dl2:
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
                            "dalle_preview_url": dalle_url_1,
                            "dalle_preview_url_s2": dalle_url_2,
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
                scene1.rendered_path = video_url_1
                scene1.render_status = "preview_ready"
                scene1.source_config = {
                    **(scene1.source_config or {}),
                    "preview_video_url": video_url_1,
                    "dalle_preview_url": dalle_url_1,
                    "scene2_fallback": True
                }
                db.session.commit()
                print(f"[Preview] Stitch failed, falling back to Scene 1 only")

            for f in [clip1_path, clip2_path, output_path]:
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
