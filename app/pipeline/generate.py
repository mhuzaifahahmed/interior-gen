import json
import logging

from sqlmodel import Session

from app.db import engine
from app.models import Project
from app.pipeline.prompts import PROMPT_VERSION, TIER_SPECS, build_negative_prompt, build_prompt
from app.providers.base import Provider
from app.storage.base import Storage

logger = logging.getLogger(__name__)

TIERS = ("economical", "mid", "premium")


def run_pipeline(project_id: str, provider: Provider, storage: Storage) -> None:
    """Runs the full 3-tier generation for a project. Intended to run as a
    background task; opens its own DB session since the request-scoped one
    will already be closed by the time this executes.
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

            for tier in TIERS:
                prompt = build_prompt(tier, room_description)
                negative_prompt = build_negative_prompt(tier)
                image_bytes = provider.generate_image(original_bytes, prompt, negative_prompt)
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
