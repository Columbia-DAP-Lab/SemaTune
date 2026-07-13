import json
from pathlib import Path

from src.optimizer.config import SimpleConfig
from src.optimizer.memory.common import (
    GEMINI_EMBEDDING_DIMENSION,
    GEMINI_EMBEDDING_MODEL,
    GEMINI_SUMMARY_MODEL,
)
from src.optimizer.model_versions import (
    DEFAULT_LLM_TEMPERATURE,
    GEMINI_31_ACTOR_MODEL,
    GEMINI_COMPARISON_ACTOR_MODEL,
    GEMINI_COMPARISON_SPECULATOR_MODEL,
    KIMI_ACTOR_MODEL,
    KIMI_ACTOR_RESPONSE_MODEL,
    KIMI_SPECULATOR_MODEL,
    MEMORY_EMBEDDING_DIMENSION,
    MEMORY_EMBEDDING_MODEL,
    MEMORY_SUMMARY_MODEL,
    PRIMARY_ACTOR_MODEL,
    PRIMARY_SPECULATOR_MODEL,
)


def test_default_models_match_artifact_pins():
    config = SimpleConfig()
    assert config.llm_model_name == PRIMARY_ACTOR_MODEL
    assert config.llm_secondary_model == PRIMARY_SPECULATOR_MODEL
    assert config.llm_temperature == DEFAULT_LLM_TEMPERATURE


def test_memory_models_match_artifact_pins():
    assert GEMINI_SUMMARY_MODEL == MEMORY_SUMMARY_MODEL
    assert GEMINI_EMBEDDING_MODEL == MEMORY_EMBEDDING_MODEL
    assert GEMINI_EMBEDDING_DIMENSION == MEMORY_EMBEDDING_DIMENSION


def test_machine_readable_manifest_matches_model_constants():
    manifest = json.loads(Path("artifact/versions.json").read_text(encoding="utf-8"))
    models = manifest["models"]
    assert models["temperature"] == DEFAULT_LLM_TEMPERATURE
    assert models["primary_actor"]["request_id"] == PRIMARY_ACTOR_MODEL
    assert models["primary_speculator"]["request_id"] == PRIMARY_SPECULATOR_MODEL
    assert models["memory_summary"]["request_id"] == MEMORY_SUMMARY_MODEL
    assert models["memory_embedding"]["request_id"] == MEMORY_EMBEDDING_MODEL
    assert models["memory_embedding"]["dimension"] == MEMORY_EMBEDDING_DIMENSION
    assert models["gemini_comparison"]["actor_request_id"] == GEMINI_COMPARISON_ACTOR_MODEL
    assert (
        models["gemini_comparison"]["speculator_request_id"]
        == GEMINI_COMPARISON_SPECULATOR_MODEL
    )
    assert models["gemini_comparison"]["additional_actor_request_id"] == GEMINI_31_ACTOR_MODEL
    assert models["kimi_comparison"]["actor_request_id"] == KIMI_ACTOR_MODEL
    assert models["kimi_comparison"]["actor_response_id"] == KIMI_ACTOR_RESPONSE_MODEL
    assert models["kimi_comparison"]["speculator_request_id"] == KIMI_SPECULATOR_MODEL
