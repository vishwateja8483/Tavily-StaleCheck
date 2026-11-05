"""Frozen evidence with exact quotations and explicitly supplied lifecycle annotations."""
from .contracts import DIGEST, day, obj, require, safe_url, sha, text, timestamp, unique


def validate_evidence(evidence):
    unique(evidence, "evidence")
    for source in evidence:
        for field in ("title", "content"):
            text(source.get(field), f"evidence {field}", 200_000)
        safe_url(source.get("url"))
        timestamp(source.get("retrieved_at"), "retrieved_at")
        require(source.get("provenance") in {"synthetic_fixture", "user_supplied_frozen"}, "unsupported evidence provenance")
        digest = source.get("content_sha256")
        require(isinstance(digest, str) and DIGEST.fullmatch(digest), "content_sha256 required")
        require(digest == sha(source["content"].encode("utf-8")), "evidence content digest mismatch")
        if source.get("published_at") is not None:
            day(source["published_at"], "published_at")
        statements = source.get("statements")
        require(isinstance(statements, list) and len(statements) <= 100, "statements must be bounded array")
        unique(statements, "statement")
        for statement in statements:
            obj(statement, "statement")
            for field in ("subject", "scope"):
                text(statement.get(field), field, 200)
            require(statement.get("assertion") == "is_supported", "unsupported evidence assertion")
            require(type(statement.get("value")) is bool, "statement value must be boolean")
            quote = text(statement.get("quote"), "quote")
            require(quote in source["content"], "quotation does not occur exactly in captured content")
            if statement.get("effective_from") is not None:
                day(statement["effective_from"], "effective_from")
            if statement.get("effective_to") is not None:
                day(statement["effective_to"], "effective_to")
                require(statement.get("effective_from") is not None and statement["effective_from"] < statement["effective_to"], "invalid half-open effective interval")
            require(statement.get("annotation_provenance") in {"synthetic_fixture", "user_reviewed"}, "statement must disclose annotation provenance")
    return evidence
