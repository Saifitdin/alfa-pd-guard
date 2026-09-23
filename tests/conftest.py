from __future__ import annotations

from pathlib import Path

import pytest

from pdguard.core.pipeline import MaskingPipeline
from pdguard.core.policy import SystemPolicy

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def pipeline() -> MaskingPipeline:
    return MaskingPipeline(ROOT / "config", ROOT / "data")


@pytest.fixture
def policy(pipeline: MaskingPipeline) -> SystemPolicy:
    """Политика нагрузочного контура: partial-маска, всё включено."""
    return pipeline.policies.by_id("loadtest")
