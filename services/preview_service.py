import os
import threading
import traceback
from typing import Optional

from openai import OpenAI

openai_client = OpenAI(
    api_key=os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY"),
    base_url=os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")
)

RUNWAY_API_KEY = os.environ.get("RUNWAY_API_KEY")


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


def generate_scene_preview_async(project_id: int, scene_plan_id: int, scene_data: dict, quality_tier: str = "good"):
    thread = threading.Thread(
        target=_run_preview_generation,
        args=(project_id, scene_plan_id, scene_data, quality_tier),
        daemon=True
    )
    thread.start()
    return thread


def _run_preview_generation(project_id: int, scene_plan_id: int, scene_data: dict, quality_tier: str):
    from app import app

    with app.app_context():
        from models import db, ScenePlan

        try:
            scene = ScenePlan.query.get(scene_plan_id)
            if not scene:
                print(f"[Preview] Scene plan {scene_plan_id} not found")
                return

            scene.render_status = "generating_preview"
            db.session.commit()

            visual_desc = scene_data.get("visual_description", "")
            script_text = scene_data.get("script_text", "")
            if not visual_desc:
                visual_desc = f"A cinematic scene depicting: {script_text}"

            print(f"[Preview] Step 1/2: Generating DALL-E base image for scene {scene.scene_index}...")
            dalle_url = generate_dalle_image(visual_desc, script_text)

            if not dalle_url:
                scene.render_status = "preview_failed"
                scene.source_config = {
                    **(scene.source_config or {}),
                    "preview_error": "Failed to generate base image"
                }
                db.session.commit()
                return

            scene.source_config = {
                **(scene.source_config or {}),
                "dalle_preview_url": dalle_url
            }
            scene.render_status = "generating_runway"
            db.session.commit()

            motion_prompt = f"Cinematic motion. {visual_desc}. Slow, deliberate camera movement. Film-quality."
            container = scene_data.get("visual_container", "fullscreen")
            if container == "split_screen":
                motion_prompt += " Split screen composition with two perspectives."
            elif container == "card":
                motion_prompt += " Content framed within a floating card element."
            elif container == "frame":
                motion_prompt += " Content within a cinematic frame border."

            print(f"[Preview] Step 2/2: Generating Runway video for scene {scene.scene_index}...")
            video_url = generate_runway_preview(
                image_url=dalle_url,
                motion_prompt=motion_prompt,
                quality_tier=quality_tier,
                duration=5,
                ratio="9:16"
            )

            scene = ScenePlan.query.get(scene_plan_id)
            if not scene:
                return

            if video_url:
                scene.rendered_path = video_url
                scene.render_status = "preview_ready"
                scene.source_config = {
                    **(scene.source_config or {}),
                    "preview_video_url": video_url,
                    "dalle_preview_url": dalle_url
                }
                print(f"[Preview] Scene {scene.scene_index} preview ready!")
            else:
                scene.render_status = "preview_failed"
                scene.source_config = {
                    **(scene.source_config or {}),
                    "preview_error": "Runway generation failed",
                    "dalle_preview_url": dalle_url
                }

            db.session.commit()

        except Exception as e:
            print(f"[Preview] Error generating preview: {e}")
            traceback.print_exc()
            try:
                scene = ScenePlan.query.get(scene_plan_id)
                if scene:
                    scene.render_status = "preview_failed"
                    scene.source_config = {
                        **(scene.source_config or {}),
                        "preview_error": str(e)
                    }
                    db.session.commit()
            except Exception:
                db.session.rollback()
