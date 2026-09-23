"""Вспомогательные функции для тестов."""

from __future__ import annotations

from pdguard.core.pipeline import MaskingPipeline


def types_in(pipeline: MaskingPipeline, text: str) -> set[str]:
    return {entity.type for entity in pipeline.detect(text)}


def entities_of(pipeline: MaskingPipeline, text: str, pd_type: str) -> list:
    return [entity for entity in pipeline.detect(text) if entity.type == pd_type]
