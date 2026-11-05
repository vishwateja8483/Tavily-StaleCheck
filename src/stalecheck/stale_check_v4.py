"""Versioned, consent-gated stale-answer assessment; replay never calls a service."""
from copy import deepcopy
from datetime import date
from html import escape
import math

from . import stale_check as legacy
from .acquisition import AcquisitionError, MockTransport, acquire, validate_plan
from .contracts import ValidationError, day, json_bytes, obj, require, sha, text
from .gateway import MockGatewayTransport, canonical

SCHEMA = "4.0"
FORMAT = "stale_answer_transcript"
VERDICTS = ("MODEL_SUPPORTED", "POTENTIALLY_CHANGED", "UNVERIFIABLE")
LIMITATIONS = (
    "All relations, observations, rationales and proposed edits are model-generated, not independently verified semantic entailment.",
    "Verbatim quotation is checked against retained excerpts only. Domain preference does not authenticate a primary source; distinct URLs do not prove independence.",
    "Current-source disagreement does not establish that an answer was once correct. Temporal change is not established by this pipeline.",
    "Publication/update dates are provider estimates, not effective dates. Retrieval time and recent publication do not establish factual currency.",
    "Evidence strength and freshness are uncalibrated metadata heuristics, not probabilities of correctness or currency: freshness only reflects how recent the newest quoted source is. Proposed edits are never automatically applied.",
    "The journey deadline is cooperative. Socket timeouts do not impose a hard bound on DNS, response headers or a detached response socket.",
    "Usage is a known subtotal plus completeness, not a monetary spending guarantee. Unknown routing means fallback behavior is unknown.",
    "Unsigned transcripts and approval records establish internal consistency, not authenticated execution, consent, source truth or live provider compatibility.",
)


def v4_config():
    return {"package_version": "0.4.0", "algorithm": "consented-model-assessment-v3", "prompt_version": "stale-check-prompts-2",
            "max_claims": 8, "max_attempts": 25, "max_seconds": 180, "network": False, "model": None}


def assessment_messages(claim, sources, verification_date):
    messages = legacy.assessment_messages(claim, sources, verification_date)
    messages[0]["content"] += (
        " CHANGED is only a proposed current-source contradiction, not proof of historical change. "
        "Publication metadata is estimated and does not prove that facts are current. "
        "Do not assert independence of different URLs. A rewrite is a suggestion requiring human review.")
    return messages


def validate_inputs(answer, verification_date, answer_date, domains, max_claims, max_attempts, max_seconds):
    text(answer, "answer_text", legacy.MAX_ANSWER_CHARS)
    verification = day(verification_date, "verification_date")
    if answer_date is not None:
        require(day(answer_date, "answer_date") <= verification, "answer_date follows verification_date")
    require(type(max_claims) is int and 1 <= max_claims <= 8, "invalid claim limit")
    require(type(max_attempts) is int and 1 <= max_attempts <= 25, "invalid journey attempt limit")
    require(type(max_seconds) in (int, float) and 0 < max_seconds <= 180 and math.isfinite(max_seconds), "invalid journey time limit")
    validate_plan({"schema_version": "1.0", "format": "reviewed_public_acquisition", "public_reviewed": True,
                   "queries": ["input validation only"], "urls": [], "primary_domains": domains})
    return verification


def approval_request(selected, domains):
    payload = {"queries": list(dict.fromkeys(c["query"] for c in selected)), "primary_domains": list(domains)}
    return {**payload, "sha256": sha(canonical(payload))}


def _selected(answer, extraction, limit):
    if extraction["status"] != "ok":
        return [], [{"stage": "extraction", "reference": None, "reason": "gateway_failed"}]
    try:
        return legacy.parse_extraction(answer, extraction["reply"]["text"], limit)
    except (ValueError, TypeError, KeyError, RecursionError):
        return [], [{"stage": "extraction", "reference": None, "reason": "invalid_reply"}]


class CollectionStop(ValidationError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _preflight_stop(budget):
    try:
        budget.check()
        require(len(budget.records) < budget.max_attempts, "journey attempt limit exhausted")
    except ValidationError:
        raise CollectionStop("journey_limit") from None
    raise CollectionStop("preflight_failed") from None


def _call(gateway, messages, ceiling, budget):
    # Check before entering the call so a budget stop is not counted as a billed attempt.
    before = len(budget.records)
    try:
        budget.check()
        require(before < budget.max_attempts, "journey attempt limit exhausted")
        reply = gateway.chat(messages, max_tokens=ceiling, budget=budget)
        return {"status": "ok", "reply": {**reply, "messages_sha256": sha(canonical(messages))}}
    except (ValidationError, ValueError, TypeError, RecursionError):
        if len(budget.records) == before:
            _preflight_stop(budget)
        return {"status": "failed", "error": "gateway call failed; no retry; usage may be unknown"}


def _research(claim, domains, key, transport, allow_network, now, budget):
    plan = {"schema_version": "1.0", "format": "reviewed_public_acquisition", "public_reviewed": True,
            "queries": [claim["query"]], "urls": [], "primary_domains": list(domains)}
    before = len(budget.records)
    try:
        budget.check()
        require(before < budget.max_attempts, "journey attempt limit exhausted")
        result = acquire(plan, allow_network=allow_network, api_key=key, transport=transport, now=now, budget=budget)
    except (ValidationError, ValueError, TypeError, RecursionError):
        if len(budget.records) == before:
            _preflight_stop(budget)
        return {"status": "failed", "query": claim["query"], "sources": [],
                "calls": len(budget.records) - before}
    try:
        json_bytes(result)  # Reject unencodable metadata before a checkpoint retains it.
    except (ValueError, TypeError, OverflowError):
        budget.records[-1]["status"] = "failed"
        return {"status": "failed", "query": claim["query"], "sources": [], "calls": len(budget.records) - before}
    sources = []
    for n, row in enumerate(result["candidates"][:legacy.MAX_SOURCES], 1):
        content = row["content"][:legacy.MAX_SOURCE_CHARS]
        sources.append({"id": f"{claim['id']}-src-{n}", "url": row["url"], "title": row["title"], "content": content,
                        "content_sha256": sha(content.encode("utf-8")), "retrieved_at": row["retrieved_at"],
                        "published_at": row["published_at"], "published_at_raw": row["published_at_raw"], "relevance_score": row["relevance_score"]})
    return {"status": "ok", "query": claim["query"], "sources": sources, "calls": len(result["requests"])}


def collect_transcript(answer_text, *, verification_date, primary_domains, gateway, approve=None, search_key=None,
                       search_transport=None, allow_network=False, answer_date=None, max_claims=5,
                       max_attempts=25, max_seconds=180, now=None, checkpoint=None, budget=None):
    from .journey import JourneyBudget
    domains = list(primary_domains)
    validate_inputs(answer_text, verification_date, answer_date, domains, max_claims, max_attempts, max_seconds)
    budget = budget if budget is not None else JourneyBudget(max_attempts=max_attempts, max_seconds=max_seconds)
    require(budget.max_attempts == max_attempts and budget.max_seconds == max_seconds, "journey budget differs from configured limits")
    transcript = {"schema_version": SCHEMA, "format": FORMAT, "answer_text": answer_text, "answer_date": answer_date,
                  "verification_date": verification_date, "primary_domains": domains,
                  "limits": {"max_claims": max_claims, "max_attempts": max_attempts, "max_seconds": max_seconds},
                  "provenance": {"gateway": "mock" if isinstance(gateway.transport, MockGatewayTransport) else "bifrost",
                                 "search": "mock" if isinstance(search_transport, MockTransport) else "tavily"},
                  "extraction": None, "approval": None, "checks": [], "attempts": [], "stop_reason": None}

    def save():
        transcript["attempts"] = deepcopy(budget.records)
        if checkpoint:
            checkpoint(deepcopy(transcript))

    try:
        save()
        try:
            transcript["extraction"] = _call(gateway, legacy.extraction_messages(answer_text, max_claims), 1200, budget)
        except CollectionStop as exc:
            transcript["extraction"] = {"status": "skipped", "reason": exc.reason}
            transcript["stop_reason"] = exc.reason
        save()
        selected, _ = _selected(answer_text, transcript["extraction"], max_claims)
        requested = approval_request(selected, domains)
        accepted = False
        if selected and approve:
            try:
                accepted = approve(deepcopy(requested)) is True
            except EOFError:
                accepted = False
        transcript["approval"] = {**requested, "approved": accepted}
        if transcript["extraction"]["status"] == "failed":
            transcript["stop_reason"] = "gateway_failed"
        elif selected and not accepted:
            transcript["stop_reason"] = "approval_declined"
        save()
        for claim in selected:
            if transcript["stop_reason"]:
                search = {"status": "skipped", "query": claim["query"], "reason": transcript["stop_reason"]}
                assessment = {"status": "skipped", "reason": transcript["stop_reason"]}
            else:
                try:
                    search = _research(claim, domains, search_key, search_transport, allow_network, now, budget)
                except CollectionStop as exc:
                    search = {"status": "skipped", "query": claim["query"], "reason": exc.reason}
                    transcript["stop_reason"] = exc.reason
                # Retain successful research before the next potentially billed operation.
                assessment = {"status": "skipped", "reason": "pending"}
                transcript["checks"].append({"claim": claim["id"], "search": search, "assessment": assessment})
                save()
                transcript["checks"].pop()
                if search["status"] == "failed":
                    transcript["stop_reason"] = "search_failed"
                    assessment = {"status": "skipped", "reason": "search_failed"}
                elif search["status"] == "skipped":
                    assessment = {"status": "skipped", "reason": search["reason"]}
                elif not search["sources"]:
                    assessment = {"status": "skipped", "reason": "no_sources"}
                else:
                    try:
                        assessment = _call(gateway, assessment_messages(claim["text"], search["sources"], verification_date), 900, budget)
                    except CollectionStop as exc:
                        assessment = {"status": "skipped", "reason": exc.reason}
                        transcript["stop_reason"] = exc.reason
                    if assessment["status"] == "failed":
                        transcript["stop_reason"] = "gateway_failed"
            transcript["checks"].append({"claim": claim["id"], "search": search, "assessment": assessment})
            save()
        make_report_v4(transcript, "fd-collected")
        return transcript
    except BaseException:
        transcript["stop_reason"] = "interrupted_or_failed"
        save()  # Preserve finalized adapter accounting even on KeyboardInterrupt.
        raise



def _validate_call(record, messages, expected):
    obj(record, "gateway record")
    if record.get("status") == "ok":
        require(set(record) == {"status", "reply"}, "invalid gateway record")
        reply = legacy._reply(record["reply"], sha(canonical(messages)))
        expected.append(("gateway", "ok", reply))
        return reply
    require(record == {"status": "failed", "error": "gateway call failed; no retry; usage may be unknown"}, "invalid failed gateway record")
    expected.append(("gateway", "failed", None))
    return None


def _usage(attempts, expected, provenance, maximum):
    require(isinstance(attempts, list) and len(attempts) == len(expected) <= maximum, "attempt ledger count mismatch")
    for record, (service, status, reply) in zip(attempts, expected):
        obj(record, "attempt")
        require(set(record) == {"service", "status", "usage", "routing"} and record["service"] == service
                and record["status"] == status, "attempt ledger order/status mismatch")
        usage = record["usage"]
        keys = ("prompt_tokens", "completion_tokens", "total_tokens") if service == "gateway" else ("credits",)
        if usage is not None:
            require(isinstance(usage, dict) and set(usage) == set(keys), "invalid attempt usage")
            require(all(v is None or (type(v) in ((int,) if service == "gateway" else (int, float)) and 0 <= v <= (10**9 if service == "gateway" else 10**12) and math.isfinite(v)) for v in usage.values()), "invalid usage number")
        if reply is not None:
            require(canonical(record["usage"]) == canonical(reply["usage"]) and canonical(record["routing"]) == canonical(reply["served_by"]), "attempt usage/routing differs from reply")
        elif service == "search":
            require(record["routing"] is None, "search cannot carry model routing")
        elif record["routing"] is not None:
            routing = record["routing"]
            require(isinstance(routing, dict) and set(routing) == {"provider", "model", "is_fallback"}, "invalid failed routing")
            require(all(routing[k] is None or (isinstance(routing[k], str) and legacy.LABEL.fullmatch(routing[k])) for k in ("provider", "model"))
                    and (routing["is_fallback"] is None or type(routing["is_fallback"]) is bool), "invalid failed routing")
    result = {"attempts": len(attempts), "provenance": provenance}
    for service, key, fields in (("gateway", "tokens", ("prompt_tokens", "completion_tokens", "total_tokens")), ("search", "credits", ("credits",))):
        records = [a for a in attempts if a["service"] == service]
        result[service + "_calls"] = len(records)
        result[key] = {"known_subtotal": {field: sum((a["usage"] or {}).get(field) or 0 for a in records) for field in fields},
                       "complete": all(a["usage"] is not None and all(a["usage"].get(f) is not None for f in fields) for a in records),
                       "scope": "mock_observations_not_spending" if provenance[service] == "mock" else "provider_reported_not_invoice"}
    gateways = [a for a in attempts if a["service"] == "gateway"]
    result["routing"] = {"observed_fallbacks": sum(bool(a["routing"] and a["routing"]["is_fallback"] is True) for a in gateways),
                         "unknown_calls": sum(a["routing"] is None or a["routing"]["is_fallback"] is None for a in gateways)}
    return result


def _strength(quotes, by_id, verification):
    dates = [date.fromisoformat(by_id[q["source_id"]]["published_at"][:10]) for q in quotes if by_id[q["source_id"]]["published_at"]]
    components = {"verbatim_quote": bool(quotes), "preferred_domain_match": any(by_id[q["source_id"]]["primary_source"] for q in quotes),
                  "distinct_urls": len({q["url"] for q in quotes}), "dated_urls": len({q["url"] for q in quotes if by_id[q["source_id"]]["published_at"]}),
                  "future_date_present": any(d > verification for d in dates)}
    score = int(components["verbatim_quote"]) + int(components["preferred_domain_match"]) + int(bool(dates) and not components["future_date_present"])
    return {"heuristic_score": score, "maximum": 3, "calibrated": False, "components": components}


def _freshness(quotes, by_id, verification):
    """Recency of the newest quoted source with a usable estimated date; it does not show that the claim is current."""
    scale = {"maximum": 100, "calibrated": False}
    if not quotes:
        return {"state": "none", "newest_source_date": None, "age_days": None, "score": None, **scale}
    dates = [date.fromisoformat(by_id[q["source_id"]]["published_at"][:10]) for q in quotes if by_id[q["source_id"]]["published_at"]]
    dates = [d for d in dates if d <= verification]
    if not dates:
        return {"state": "undated", "newest_source_date": None, "age_days": None, "score": None, **scale}
    newest = max(dates)
    age = (verification - newest).days
    window = legacy.RECENT_DAYS
    state = "current" if age <= legacy.FRESH_DAYS else "recent" if age <= window else "dated"
    score = 0 if age >= window else (100 * (window - age) + window // 2) // window
    return {"state": state, "newest_source_date": newest.isoformat(), "age_days": age, "score": score, **scale}


def make_report_v4(request, run_id):
    obj(request, "transcript")
    require(set(request) == {"schema_version", "format", "answer_text", "answer_date", "verification_date", "primary_domains", "limits", "provenance",
                             "extraction", "approval", "checks", "attempts", "stop_reason"}, "invalid v4 transcript fields")
    require(request["schema_version"] == SCHEMA and request["format"] == FORMAT, "unsupported v4 transcript")
    limits = obj(request["limits"], "limits")
    require(set(limits) == {"max_claims", "max_attempts", "max_seconds"}, "invalid journey limits")
    answer, domains = request["answer_text"], request["primary_domains"]
    verification = validate_inputs(answer, request["verification_date"], request["answer_date"], domains, **limits)
    provenance = obj(request["provenance"], "provenance")
    require(set(provenance) == {"gateway", "search"} and provenance["gateway"] in ("mock", "bifrost") and provenance["search"] in ("mock", "tavily"), "invalid provenance")
    expected, stopped = [], None
    extraction = obj(request["extraction"], "extraction")
    if extraction in ({"status": "skipped", "reason": r} for r in ("journey_limit", "preflight_failed")):
        stopped = extraction["reason"]
    elif _validate_call(extraction, legacy.extraction_messages(answer, limits["max_claims"]), expected) is None:
        stopped = "gateway_failed"
    selected, rejected = _selected(answer, extraction, limits["max_claims"])
    approval = obj(request["approval"], "approval")
    require(set(approval) == {"queries", "primary_domains", "sha256", "approved"} and type(approval["approved"]) is bool, "invalid exact-query approval")
    require({k: approval[k] for k in ("queries", "primary_domains", "sha256")} == approval_request(selected, domains), "approval does not bind exact queries/domains")
    require(bool(selected) or not approval["approved"], "empty selection cannot be approved")
    if selected and not approval["approved"]:
        stopped = "approval_declined"
    checks = request["checks"]
    require(isinstance(checks, list) and len(checks) == len(selected), "checks must cover selected claims")
    claims, evidence = [], []
    for claim, check in zip(selected, checks):
        require(isinstance(check, dict) and set(check) == {"claim", "search", "assessment"} and check["claim"] == claim["id"], "invalid ordered check")
        search, assessment = obj(check["search"], "search"), obj(check["assessment"], "assessment")
        sources, quotes, proposed, observed, rationale, rewrite = [], [], None, None, None, None
        code = None
        if stopped:
            require(search == {"status": "skipped", "query": claim["query"], "reason": stopped}
                    and assessment == {"status": "skipped", "reason": stopped}, "calls continued after terminal stop")
            code = stopped.upper()
        elif search in ({"status": "skipped", "query": claim["query"], "reason": r} for r in ("journey_limit", "preflight_failed")):
            require(assessment == {"status": "skipped", "reason": search["reason"]}, "invalid preflight skip")
            stopped, code = search["reason"], search["reason"].upper()
        else:
            require(set(search) == {"status", "query", "sources", "calls"} and search["status"] in ("ok", "failed")
                    and search["query"] == claim["query"] and type(search["calls"]) is int and search["calls"] == 1, "invalid search record")
            expected.append(("search", search["status"], None))
            require(isinstance(search["sources"], list) and len(search["sources"]) <= legacy.MAX_SOURCES, "too many sources")
            sources = [legacy._source(row, claim["id"], i, domains) for i, row in enumerate(search["sources"], 1)]
            require(len({s["url"] for s in sources}) == len(sources) and all(s["primary_source"] for s in sources), "duplicate/outside-domain sources")
            if search["status"] == "failed":
                require(not sources and assessment == {"status": "skipped", "reason": "search_failed"}, "invalid failed search")
                stopped, code = "search_failed", "SEARCH_FAILED"
            elif not sources:
                require(assessment == {"status": "skipped", "reason": "no_sources"}, "assessment without sources")
                code = "NO_SOURCES"
            elif assessment in ({"status": "skipped", "reason": r} for r in ("journey_limit", "preflight_failed")):
                stopped, code = assessment["reason"], assessment["reason"].upper()
            else:
                reply = _validate_call(assessment, assessment_messages(claim["text"], sources, request["verification_date"]), expected)
                if reply is None:
                    stopped, code = "gateway_failed", "GATEWAY_FAILED"
                else:
                    try:
                        proposed, observed, rationale, rewrite, quotes, dropped = legacy.parse_assessment(sources, reply["text"])
                        rejected += [{"stage": "assessment", "reference": claim["id"], "reason": r} for r in dropped]
                    except (ValueError, TypeError, KeyError, RecursionError):
                        code = "ASSESSMENT_INVALID_REPLY"
        if code is None:
            verdict, code = legacy._decide(proposed, quotes, claim["text"])
            verdict = {"SUPPORTED": "MODEL_SUPPORTED", "CHANGED": "POTENTIALLY_CHANGED", "UNVERIFIABLE": "UNVERIFIABLE"}[verdict]
            code = {"VERIFIED_SUPPORTED": "MODEL_SUPPORT_WITH_VERBATIM_QUOTE", "VERIFIED_CHANGED": "MODEL_CONTRADICTION_WITH_VERBATIM_QUOTE"}.get(code, code.replace("VERIFIED", "VERBATIM"))
        else:
            verdict = "UNVERIFIABLE"
        by_id = {s["id"]: s for s in sources}
        claims.append({**claim, "verdict": verdict, "reason_code": code, "proposed_verdict": proposed,
                       "temporal_change_established": False, "evidence_strength": _strength(quotes, by_id, verification),
                       "freshness": _freshness(quotes, by_id, verification),
                       "evidence": [{**q, "relation_model_generated": True} for q in quotes],
                       "inference": {"model_generated": True, "observed": observed, "rationale": rationale,
                                     "suggested_rewrite": rewrite if verdict == "POTENTIALLY_CHANGED" else None,
                                     "automatically_applied": False, "human_review_required": True}})
        evidence += [{**{k: v for k, v in s.items() if k != "primary_source"}, "claim_id": claim["id"], "preferred_domain_match": s["primary_source"],
                      "publication_is_estimate": True, "effective_from": None, "effective_to": None} for s in sources]
    require(request["stop_reason"] == stopped, "stop reason differs from execution records")
    usage = _usage(request["attempts"], expected, provenance, limits["max_attempts"])
    counts = {v: sum(c["verdict"] == v for c in claims) for v in VERDICTS}
    limitations = list(LIMITATIONS) + ([legacy.MOCK_LIMITATION] if "mock" in provenance.values() else [])
    return {"project_id": "stalecheck", "run_id": run_id, "mode": "replay", "schema_version": SCHEMA, "version": "0.4.0",
            "title": "StaleCheck model-assisted stale-answer assessment", "summary": f"{len(claims)} selected claim(s): {counts['MODEL_SUPPORTED']} model-supported, {counts['POTENTIALLY_CHANGED']} potentially changed, {counts['UNVERIFIABLE']} unverifiable.",
            "answer_text": answer, "answer_date": request["answer_date"], "verification_date": request["verification_date"],
            "primary_domains": list(domains), "claims": claims, "evidence": evidence, "counts": counts, "rejected_proposals": rejected,
            "approval": approval, "stop_reason": stopped, "usage": usage, "limitations": limitations,
            "input_sha256": sha(json_bytes(request)), "config_sha256": sha(json_bytes(v4_config()))}


def render_html(report):
    """Script-free, escaped document. Proposed edits remain separate from the answer."""
    e = lambda value: escape(str(value))
    glance = ''
    if report["claims"]:
        rows = ''.join(f'<tr><td>{e(c["id"])}</td><td>{e(c["text"])}</td><td>{e(c["category"])}</td><td>{e(c["verdict"])}</td>'
                       f'<td>{e("not scored" if c["freshness"]["score"] is None else c["freshness"]["score"])}</td></tr>' for c in report["claims"])
        glance = ('<section><h2>At a glance</h2><table><caption>One row per selected claim. Freshness (0-100) is the recency of the quoted evidence, not proof that the claim is current.</caption>'
                  '<colgroup><col style="width:9%"><col style="width:35%"><col style="width:14%"><col style="width:28%"><col style="width:14%"></colgroup>'
                  '<tr><th scope="col">Claim</th><th scope="col">Text</th><th scope="col">Category</th><th scope="col">Verdict</th><th scope="col">Freshness</th></tr>' + rows + '</table></section>')
    parts = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">',
             '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">',
             '<title>StaleCheck assessment</title><style>body{font:16px/1.6 system-ui,sans-serif;max-width:65rem;margin:2rem auto;padding:0 1rem;color:#202020}article,section{border:1px solid #aaa;padding:1rem;margin:1rem 0}pre,blockquote{white-space:pre-wrap;overflow-wrap:anywhere}a{overflow-wrap:anywhere}dt{font-weight:bold}dd{margin:0 0 .6rem}table{border-collapse:collapse;width:100%}td,th{border:1px solid #aaa;padding:.5rem;text-align:left;vertical-align:top;overflow-wrap:anywhere}table{table-layout:fixed}article,section,p,h2{overflow-wrap:anywhere}</style></head><body>',
             f'<h1>{e(report["title"])}</h1><p>{e(report["summary"])}</p>',
             f'<p>Assessment date: {e(report["verification_date"])}. Answer date: {e(report["answer_date"] or "unknown")}. Stop reason: {e(report["stop_reason"] or "none")}.</p>',
             '<p>Model-assisted assessment, not proof of historical change or factual truth. All proposed edits require human review.</p>',
             glance,
             f'<section><h2>Full original answer</h2><pre>{e(report["answer_text"])}</pre></section>',
             f'<section><h2>Exact research approval</h2><pre>{e(json_bytes(report["approval"]).decode())}</pre></section>']
    sources = {s["id"]: s for s in report["evidence"]}
    for claim in report["claims"]:
        inference = claim["inference"]
        fresh = claim["freshness"]
        scored = (f'score {fresh["score"]}/100, newest quoted source {fresh["newest_source_date"]}, {fresh["age_days"]} day(s) before the assessment date'
                  if fresh["score"] is not None else "not scored")
        parts += [f'<article><h2>{e(claim["id"])}: {e(claim["verdict"])}</h2><p>{e(claim["reason_code"])}. Answer span [{claim["span"]["start"]}, {claim["span"]["end"]}).</p>',
                  f'<table><caption>Original excerpt and proposed edit (never auto-applied)</caption><tr><th scope="col">Original</th><th scope="col">Model-generated proposal</th></tr><tr><td>{e(claim["text"])}</td><td>{e(inference["suggested_rewrite"] or "No supported proposal retained")}</td></tr></table>',
                  f'<p>Category (model-labelled): {e(claim["category"])}. Freshness of the quoted evidence: {e(fresh["state"])} ({e(scored)}). Evidence recency, not proof that the claim is current.</p>',
                  f'<p>Query: {e(claim["query"])}. Time sensitivity (model-generated): {e(claim["time_sensitivity"]["level"])} — {e(claim["time_sensitivity"]["reason"])}</p>',
                  f'<h3>Uncalibrated evidence-strength components</h3><pre>{e(json_bytes(claim["evidence_strength"]).decode())}</pre><h3>Verbatim source quotations</h3>']
        for quote in claim["evidence"]:
            source = sources[quote["source_id"]]
            parts.append(f'<blockquote>{e(quote["quote"])}<br><a href="{e(quote["url"])}" target="_blank" rel="noopener noreferrer">{e(source["title"])}</a><br>Model-labelled relation: {e(quote["relation"])}. Retrieved: {e(source["retrieved_at"])}. Estimated publication/update: {e(source["published_at"] or "unknown")}. Effective dates: unknown.</blockquote>')
        if not claim["evidence"]:
            parts.append('<p>No retained verbatim quotation.</p>')
        retrieved = [s for s in report["evidence"] if s["claim_id"] == claim["id"]]
        if retrieved:
            parts.append('<h3>Retrieved sources (not evidence unless quoted above)</h3><ul>' + ''.join(
                f'<li><a href="{e(s["url"])}" target="_blank" rel="noopener noreferrer">{e(s["title"])}</a>. Estimated publication/update: {e(s["published_at"] or "unknown")}.</li>'
                for s in retrieved) + '</ul>')
        parts.append(f'<h3>Model-generated reasoning</h3><p>{e(inference["observed"] or "No observation")}<br>{e(inference["rationale"] or "No rationale")}</p></article>')
    parts += [f'<section><h2>Rejected or unselected proposals</h2><pre>{e(json_bytes(report["rejected_proposals"]).decode())}</pre></section>',
              f'<section><h2>Usage: known subtotals and completeness</h2><pre>{e(json_bytes(report["usage"]).decode())}</pre></section>',
              '<h2>Limitations</h2><ul>' + ''.join(f'<li>{e(line)}</li>' for line in report['limitations']) + '</ul></body></html>']
    return ''.join(parts)
