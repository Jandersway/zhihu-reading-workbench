from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    project_root: Path
    source_root: str
    data_dir: Path
    kimi_base_url: str
    kimi_api_key: str
    redis_url: str | None

    @classmethod
    def from_env(cls) -> "Settings":
        local_home = Path.home() / ".zhihu-reading-workbench"
        root = Path(os.getenv("ZHIHU_PROJECT_ROOT") or local_home).expanduser().resolve()
        default_data = local_home / "data" / "answers"
        return cls(
            project_root=root,
            source_root=os.getenv("ZHIHU_SOURCE_ROOT") or str(root),
            data_dir=Path(os.getenv("ZHIHU_AGENT_DATA_DIR") or default_data).expanduser().resolve(),
            kimi_base_url=os.getenv("KIMI_BASE_URL", "https://api.kimi.com/coding/v1"),
            kimi_api_key=os.getenv("KIMI_API_KEY", ""),
            redis_url=os.getenv("REDIS_URL") or None,
        )
