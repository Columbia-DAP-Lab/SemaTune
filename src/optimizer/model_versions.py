#!/usr/bin/env python3
"""Model identifiers frozen for the SOSP '26 artifact.

Hosted Gemini identifiers are provider-managed endpoints, not downloadable
weight digests.  ``MODEL_REFERENCE_DATE_UTC`` therefore accompanies the exact
request IDs, and callers should preserve the model identity returned by each
API response when the provider exposes one.
"""

MODEL_REFERENCE_DATE_UTC = "2026-03-30"

PRIMARY_ACTOR_MODEL = "gemini-2.5-flash"
PRIMARY_SPECULATOR_MODEL = "gemini-2.5-flash-lite"
DEFAULT_LLM_TEMPERATURE = 0.7

MEMORY_SUMMARY_MODEL = PRIMARY_SPECULATOR_MODEL
MEMORY_EMBEDDING_MODEL = "gemini-embedding-001"
MEMORY_EMBEDDING_DIMENSION = 768

GEMINI_31_ACTOR_MODEL = "gemini-3.1-pro-preview"
GEMINI_COMPARISON_ACTOR_MODEL = "gemini-3-flash-preview"
GEMINI_COMPARISON_SPECULATOR_MODEL = "gemini-3.1-flash-lite-preview"

KIMI_ACTOR_MODEL = "moonshotai/kimi-k2-thinking"
KIMI_ACTOR_RESPONSE_MODEL = "moonshotai/kimi-k2-thinking-20251106"
KIMI_SPECULATOR_MODEL = "moonshotai/kimi-k2"
