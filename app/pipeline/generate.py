import json
import logging

from sqlmodel import Session

from app.db import engine
from app.models import Project
from app.pipeline.prompts import (
    PROMPT_VERSION,
    TIER_SPECS,
    build_negative_prompt,
    build_prompt,
    get_strength,
)
from app.providers.base import Provider
from app.storage.base import Storage

logger = logging.getLogger(__name__)

TIERS = ("economical", "mid", "premium")


def run_pipeline(
    project_id: str,
    provider: Provider,
    storage: Storage,
    user_style_notes: str | None = None,
) -> None:
    """Runs the full 3-tier generation for a project. Intended to run as a
    background task; opens its own DB session since the request-scoped one
    will already be closed by the time this executes.

    user_style_notes is the optional free-text style prompt the user typed in
    (static/index.html's style-prompt input) - passed through to every tier's
    build_prompt() call, see prompts.py for exactly how it's incorporated.
    """
    with Session(engine) as session:
        project = session.get(Project, project_id)
        if project is None:
            logger.error("run_pipeline: project %s not found", project_id)
            return

        project.status = "running"
        session.add(project)
        session.commit()

        try:
            original_bytes = storage.get(project.original_key)

            try:
                room_description = provider.describe_room(original_bytes)
            except Exception:
                logger.exception("describe_room failed for project %s; continuing without it", project_id)
                room_description = None

            project.room_description = room_description
            session.add(project)
            session.commit()

            try:
                tier_notes = provider.generate_tier_notes(original_bytes)
            except Exception:
                logger.exception("generate_tier_notes failed for project %s; continuing without it", project_id)
                tier_notes = {}

            for tier in TIERS:
                prompt = build_prompt(tier, room_description, tier_notes.get(tier), user_style_notes)
                negative_prompt = build_negative_prompt(tier)
                strength = get_strength(tier)
                image_bytes = provider.generate_image(original_bytes, prompt, negative_prompt, strength)
                key = f"{project_id}/{tier}.png"
                storage.put(key, image_bytes, content_type="image/png")
                setattr(project, f"{tier}_key", key)
                session.add(project)
                session.commit()

            project.status = "done"
            project.meta_json = json.dumps(
                {
                    "prompt_version": PROMPT_VERSION,
                    "tier_specs": list(TIER_SPECS.keys()),
                    "tier_notes": tier_notes,
                    "user_style_notes": user_style_notes,
                }
            )
            session.add(project)
            session.commit()

        except Exception as exc:
            logger.exception("pipeline failed for project %s", project_id)
            project.status = "failed"
            project.error = str(exc)
            session.add(project)
            session.commit()
