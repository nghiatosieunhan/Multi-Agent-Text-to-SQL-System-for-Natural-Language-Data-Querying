"""Named experiment profiles used by the evaluator and graph."""


PROFILE_OPTIONS = {
    "full": {
        "cache_enabled": True,
        "few_shot_enabled": True,
        "planner_enabled": True,
        "validator_enabled": True,
        "semantic_validation_enabled": True,
        "projection_validation_enabled": True,
        "semantic_warning_repair_enabled": True,
    },
    "full_no_cache": {
        "cache_enabled": False,
        "few_shot_enabled": True,
        "planner_enabled": True,
        "validator_enabled": True,
        "schema_pruning_mode": "auto",
        "semantic_validation_enabled": True,
        "projection_validation_enabled": True,
        "semantic_warning_repair_enabled": True,
    },
    "no_rag": {
        "cache_enabled": False,
        "few_shot_enabled": False,
        "planner_enabled": True,
        "validator_enabled": True,
    },
    "no_planner": {
        "cache_enabled": False,
        "few_shot_enabled": True,
        "planner_enabled": False,
        "validator_enabled": True,
    },
    "no_query_spec": {
        "cache_enabled": False,
        "few_shot_enabled": True,
        "query_spec_enabled": False,
        "planner_enabled": True,
        "validator_enabled": True,
        "schema_pruning_mode": "auto",
        "semantic_validation_enabled": True,
        "projection_validation_enabled": True,
        "semantic_warning_repair_enabled": True,
    },
    "no_validator": {
        "cache_enabled": False,
        "few_shot_enabled": True,
        "planner_enabled": True,
        "validator_enabled": False,
        "self_correction_enabled": False,
        "projection_validation_enabled": False,
        "projection_contract_enabled": False,
    },
    "auto_bypass": {
        "cache_enabled": False,
        "few_shot_enabled": True,
        "planner_enabled": True,
        "validator_enabled": True,
        "schema_pruning_mode": "auto",
    },
    "forced_pruning": {
        "cache_enabled": False,
        "few_shot_enabled": True,
        "planner_enabled": True,
        "validator_enabled": True,
        "schema_pruning_mode": "force",
    },
    "single_zero_shot": {"baseline": "zero_shot"},
    "single_structured": {"baseline": "structured"},
}


def get_profile_options(profile: str, overrides: dict | None = None) -> dict:
    if profile not in PROFILE_OPTIONS:
        valid = ", ".join(sorted(PROFILE_OPTIONS))
        raise ValueError(f"Unknown evaluation profile '{profile}'. Valid profiles: {valid}")
    options = dict(PROFILE_OPTIONS[profile])
    if overrides:
        options.update(overrides)
    return options
