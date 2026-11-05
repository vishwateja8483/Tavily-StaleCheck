"""Allowlisted domain versions; validation failures never trigger a fallback."""
from .contracts import obj, require


V1_VERSION = "0.1.0"
V1_LIMITATIONS = (
    "Offline replay: no model, retrieval, paid API, network, or credential access.",
    "Synthetic fixtures demonstrate plumbing, not official source truths or measured model/retrieval effectiveness.",
    "User-supplied annotations are trusted inputs, not independently validated semantic entailment. Exact quotes and hashes establish consistency, not truth or authenticity.",
    "Only exact subject/scope is_supported point-in-time assertions are supported; unsupported or ambiguous prose abstains.",
    "Intervals are [effective_from, effective_to), with null end explicitly meaning open-ended applicability supplied by the annotator; missing start is unknown.",
    "Retrieval/publication dates never substitute for effective dates. Future retrieval can capture historic applicability but is not proof of what was known then.",
)


def v1_config():
    # A fresh value keeps caller mutations out of retained-version verification.
    return {"package_version": V1_VERSION, "algorithm": "reviewed-lifecycle-intervals-v1", "max_claims": 5, "network": False, "model": None}


def validate_v1_config(value):
    obj(value, "config")
    expected = v1_config()
    require(set(value) == set(expected), "unsupported or modified config/version")
    require(all(type(value[key]) is type(expected[key]) and value[key] == expected[key] for key in expected), "unsupported or modified config/version")


def report_builder(request, config=None):
    obj(request, "input")
    if request.get("schema_version") == "2.0":
        require(request.get("format") == "reviewed_lifecycle_claims", "input format does not match schema version 2.0")
        from .report_v2 import make_report_v2, v2_config
        if config is not None:
            obj(config, "config")
            expected = v2_config()
            require(set(config) == set(expected) and all(type(config[key]) is type(expected[key]) and config[key] == expected[key] for key in expected), "unsupported or modified v2 config/version")
        return make_report_v2
    if request.get("schema_version") == "4.0":
        require(request.get("format") == "stale_answer_transcript", "input format does not match schema version 4.0")
        from .stale_check_v4 import make_report_v4, v4_config
        if config is not None:
            obj(config, "config")
            expected = v4_config()
            require(set(config) == set(expected) and all(type(config[key]) is type(expected[key]) and config[key] == expected[key] for key in expected), "unsupported or modified v4 config/version")
        return make_report_v4
    if request.get("schema_version") == "3.0":
        require(request.get("format") == "stale_answer_transcript", "input format does not match schema version 3.0")
        from .stale_check import make_report_v3, v3_config
        if config is not None:
            obj(config, "config")
            expected = v3_config()
            require(set(config) == set(expected) and all(type(config[key]) is type(expected[key]) and config[key] == expected[key] for key in expected), "unsupported or modified v3 config/version")
        return make_report_v3
    require(request.get("schema_version") == "1.0", "unsupported input schema version")
    if config is not None:
        validate_v1_config(config)
    from .report import make_report_v1
    return make_report_v1
