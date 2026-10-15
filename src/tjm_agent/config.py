"""Settings loaded from the environment / .env. Nothing here is secret except the API key,
which is read by the Anthropic SDK from ANTHROPIC_API_KEY and never logged."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .grounding.screenseeker import SearchConfig

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    planner_provider: str
    planner_model: str
    planner_effort: str
    check_effort: str
    grounder_repo: str
    search: SearchConfig
    output_dir: Path
    posts_url: str = "https://jsonplaceholder.typicode.com/posts"
    app_name: str = "TextEdit"
    icon_target: str = "TextEdit application icon on the desktop"


def load() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")
    provider = os.getenv("PLANNER_PROVIDER", "anthropic").strip()
    if provider != "anthropic":
        raise SystemExit(f"PLANNER_PROVIDER={provider!r} is not implemented; use 'anthropic'.")
    return Settings(
        planner_provider=provider,
        planner_model=os.getenv("PLANNER_MODEL", "claude-opus-5-5"),
        planner_effort=os.getenv("PLANNER_EFFORT", "low"),
        check_effort=os.getenv("CHECK_EFFORT", "medium"),
        grounder_repo=os.getenv("GROUNDER_REPO", "mlx-community/UI-Venus-1.5-2B-6bit"),
        search=SearchConfig(),
        output_dir=Path(os.getenv("OUTPUT_DIR", str(Path.home() / "Desktop" / "tjm-project"))),
    )
