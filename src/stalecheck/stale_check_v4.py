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



_REPORT_STYLE = """
:root { color-scheme: light; --ink: #12161a; --ink-2: #3a444d; --ink-3: #56616b; --hair: #c9d1d8; --faint: #e3e8ec;
  --wash: #f4f6f8; --desk: #e8ecef; --sheet: #fff; --red: #c4231a; --red-wash: #fdf0ee; --blue: #1f4fa3; --grey: #5b656e;
  --sans: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --mono: ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, monospace; }
* { box-sizing: border-box; }
html, body { margin: 0; background: var(--desk); }
body { color: var(--ink); font: 15px/1.55 var(--sans); -webkit-text-size-adjust: 100%; }
main { position: relative; max-width: 62rem; margin: clamp(0px, 2.4vw, 24px) auto; padding: clamp(24px, 4.2vw, 46px);
  overflow-wrap: anywhere; background: var(--sheet); box-shadow: 0 1px 2px #12161a1f, 0 16px 38px -16px #12161a59; }
main::before { content: ""; position: absolute; inset: 9px; border: 1.5px solid var(--ink); pointer-events: none; }
h1, h2, h3, p, pre, figure, blockquote, dl, dd { margin: 0; }
h1 { font: 800 clamp(1.75rem, 4.4vw, 2.6rem)/1.04 var(--sans); letter-spacing: -.04em; }
h2 { font: 800 1.1875rem/1.2 var(--sans); letter-spacing: -.02em; }
h3 { font: 700 .9375rem/1.3 var(--sans); }
p + p { margin-top: .55rem; }
a { color: var(--blue); text-underline-offset: .18em; }
a:focus-visible, summary:focus-visible { outline: 2px solid var(--blue); outline-offset: 3px; }
.report-mode { display: flex; align-items: center; gap: 8px; margin-bottom: 14px; color: var(--ink-3); font-size: .8125rem; font-weight: 650; }
.brand { width: 18px; height: 18px; flex: none; fill: none; stroke: currentColor; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.report-review { max-width: 62ch; margin-top: 10px; color: var(--ink-2); }
.title-block { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); margin-top: 22px;
  border-top: 1px solid var(--ink); border-left: 1px solid var(--ink); }
.title-block > div { min-width: 0; padding: 8px 11px 10px; border-right: 1px solid var(--ink); border-bottom: 1px solid var(--ink); }
.title-block dt { color: var(--ink-3); font-size: .75rem; line-height: 1.3; }
.title-block dd { margin-top: 3px; font: 650 .875rem/1.3 var(--mono); font-variant-numeric: tabular-nums; }
.title-block .note { display: block; margin-top: 3px; color: var(--ink-3); font: 400 .6875rem/1.35 var(--sans); }
.notice { margin-top: 22px; padding: 10px 14px; border: 1px dashed var(--ink-3); background: var(--wash); }
.sheet-section { margin-top: 32px; }
.sheet-section > h2 { padding-bottom: 7px; margin-bottom: 14px; border-bottom: 1px solid var(--ink); }
.section-note { max-width: 68ch; margin: -4px 0 14px; color: var(--ink-3); font-size: .875rem; }
.shape { display: inline-block; width: 1.05em; height: 1.05em; flex: none; vertical-align: -.17em; }
.s-supported { fill: var(--ink); }
.s-changed { fill: var(--red); }
.s-unverifiable { fill: var(--grey); }
.shape text { fill: #fff; font-family: var(--mono); font-weight: 700; }
.verdict-counts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); padding: 0; margin: 0 0 20px;
  list-style: none; border: 1px solid var(--ink); }
.verdict-counts li { display: grid; grid-template-columns: auto 1fr; align-items: center; column-gap: 12px;
  padding: 12px 14px; color: var(--ink-2); font-size: .875rem; line-height: 1.25; }
.verdict-counts li + li { border-left: 1px solid var(--ink); }
.verdict-counts .shape { grid-row: span 2; width: 28px; height: 28px; }
.verdict-counts strong { color: var(--ink); font: 800 1.75rem/1 var(--sans); font-variant-numeric: tabular-nums; }
table { border-collapse: collapse; width: 100%; }
caption { padding: 0 0 8px; color: var(--ink-3); font-size: .8125rem; text-align: left; }
td, th { padding: 9px 10px; text-align: left; vertical-align: top; }
th { border-bottom: 1px solid var(--ink); color: var(--ink-3); font-size: .75rem; font-weight: 650; }
td { border-bottom: 1px solid var(--hair); font-size: .875rem; }
.claims-overview td:first-child { font: 700 .8125rem/1.6 var(--mono); }
.claims-overview td:nth-child(4) { font-weight: 650; }
.claims-overview td:nth-child(4) .shape { margin-right: 7px; }
.claims-overview td:last-child { position: relative; padding-bottom: 19px; font: 700 .875rem/1.5 var(--mono); font-variant-numeric: tabular-nums; }
.claims-overview td:last-child::after { content: ""; position: absolute; left: 10px; bottom: 9px; width: 72px; height: 3px;
  background: linear-gradient(var(--ink), var(--ink)) 0 0 / calc((365 - var(--age, 365)) / 365 * 100%) 100% no-repeat, var(--faint); }
.answer-markup { padding: 16px 20px; border: 1px solid var(--ink); font-size: 1rem; line-height: 2.1; white-space: pre-wrap; }
mark { color: inherit; background: none; }
.u-changed { background: var(--red-wash); text-decoration: underline wavy var(--red) 1.5px; text-underline-offset: 5px; text-decoration-skip-ink: none; }
.u-unverifiable { text-decoration: underline dotted var(--grey) 2px; text-underline-offset: 5px; }
.u-supported { text-decoration: underline solid var(--ink) 1px; text-underline-offset: 5px; }
.callout { display: inline-block; width: 26px; height: 26px; margin: 0 3px; vertical-align: middle; }
.callout .shape { display: block; width: 100%; height: 100%; }
.claim-card { position: relative; margin-top: 28px; padding: clamp(18px, 3vw, 26px); scroll-margin-top: 16px;
  border: 1px solid var(--ink); background: var(--sheet); }
.claim-card[data-verdict="UNVERIFIABLE"] { border-style: dashed; border-color: var(--ink-2); }
.claim-card:target { box-shadow: 0 0 0 3px var(--sheet), 0 0 0 5px var(--blue); }
.claim-card[data-verdict="POTENTIALLY_CHANGED"] { --r: 10px; --w: 2.2px; padding: calc(var(--r) + 20px); border: 0; }
.claim-card[data-verdict="POTENTIALLY_CHANGED"]::before { content: ""; position: absolute; inset: 0; pointer-events: none;
  background-image:
    radial-gradient(ellipse 50% 100% at 50% 100%, transparent calc(100% - var(--w) - .5px), var(--red) calc(100% - var(--w)), var(--red) calc(100% - .5px), transparent 100%),
    radial-gradient(ellipse 50% 100% at 50% 0, transparent calc(100% - var(--w) - .5px), var(--red) calc(100% - var(--w)), var(--red) calc(100% - .5px), transparent 100%),
    radial-gradient(ellipse 100% 50% at 100% 50%, transparent calc(100% - var(--w) - .5px), var(--red) calc(100% - var(--w)), var(--red) calc(100% - .5px), transparent 100%),
    radial-gradient(ellipse 100% 50% at 0 50%, transparent calc(100% - var(--w) - .5px), var(--red) calc(100% - var(--w)), var(--red) calc(100% - .5px), transparent 100%);
  background-size: calc(var(--r) * 2) var(--r), calc(var(--r) * 2) var(--r), var(--r) calc(var(--r) * 2), var(--r) calc(var(--r) * 2);
  background-position: 0 0, 0 100%, 0 0, 100% 0;
  background-repeat: round no-repeat, round no-repeat, no-repeat round, no-repeat round; }
@keyframes cloud-in { from { clip-path: inset(0 100% 0 0); } to { clip-path: inset(0 0 0 0); } }
@media (prefers-reduced-motion: no-preference) {
  .claim-card[data-verdict="POTENTIALLY_CHANGED"]:target::before { animation: cloud-in 800ms cubic-bezier(.2, .7, .2, 1) both; }
}
.claims > .claim-card:first-of-type { margin-top: 0; }
.claim-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.claim-head h2 { display: flex; align-items: center; gap: 10px; font-size: 1.25rem; }
.claim-head h2 .shape { width: 26px; height: 26px; }
.score { flex: none; font-family: var(--mono); line-height: 1; text-align: right; }
.score strong { font-size: 2rem; font-weight: 800; letter-spacing: -.04em; font-variant-numeric: tabular-nums; }
.score span { color: var(--ink-3); font-size: .875rem; }
.score small { display: block; margin-top: 5px; color: var(--ink-3); font: 400 .75rem/1.2 var(--sans); }
.claim-comparison { margin-top: 18px; }
.claim-comparison h3 { margin-bottom: 8px; }
.comparison-columns { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); border: 1px solid var(--ink); }
.comparison-columns > div { min-width: 0; padding-bottom: 12px; }
.comparison-columns > div + div { border-left: 1px solid var(--ink); }
.comparison-columns strong { display: block; margin-bottom: 10px; padding: 7px 12px; border-bottom: 1px solid var(--ink);
  background: var(--wash); font-size: .75rem; line-height: 1.3; }
.comparison-columns p { padding: 0 12px; white-space: pre-wrap; }
[data-verdict="POTENTIALLY_CHANGED"] .comparison-columns > div + div { background: var(--red-wash); }
.claim-metadata { max-width: 72ch; margin-top: 16px; color: var(--ink-2); font-size: .875rem; }
.ruler { margin-top: 14px; }
.ruler-cap { display: block; margin-bottom: 26px; color: var(--ink-3); font-size: .75rem; line-height: 1.3; }
.ruler-track { position: relative; height: 18px; border-bottom: 1px solid var(--ink); }
.ruler-dim { position: absolute; right: 0; bottom: 0; width: min(100%, calc(var(--age, 365) / 365 * 100%)); min-width: 3px; height: 18px;
  border-inline: 1px solid var(--ink); background: linear-gradient(var(--ink), var(--ink)) 0 50% / 100% 1px no-repeat; }
.ruler-dim::before, .ruler-dim::after { content: ""; position: absolute; top: 50%; width: 7px; height: 7px; margin-top: -3.5px; background: var(--ink); }
.ruler-dim::before { left: 0; clip-path: polygon(0 50%, 100% 0, 100% 100%); }
.ruler-dim::after { right: 0; clip-path: polygon(100% 50%, 0 0, 0 100%); }
.ruler-dim b { position: absolute; right: 0; bottom: calc(100% + 4px); color: var(--ink); font: 700 .75rem/1 var(--mono); white-space: nowrap; }
.ruler[data-scored="false"] .ruler-dim { width: 100%; border-color: var(--ink-3);
  background: repeating-linear-gradient(to right, var(--ink-3) 0 6px, transparent 6px 11px) 0 50% / 100% 1px no-repeat; }
.ruler[data-scored="false"] .ruler-dim::before, .ruler[data-scored="false"] .ruler-dim::after { display: none; }
.ruler-ticks { position: relative; height: 24px; color: var(--ink-3); font: .6875rem/1 var(--mono); }
.ruler-ticks i { position: absolute; top: 0; padding-top: 9px; font-style: normal; white-space: nowrap; transform: translateX(-50%); }
.ruler-ticks i::before { content: ""; position: absolute; top: 0; left: 50%; width: 1px; height: 6px; background: var(--ink); }
.ruler-ticks .k0 { transform: none; }
.ruler-ticks .k0::before { left: 0; }
.ruler-ticks .k4 { transform: translateX(-100%); }
.ruler-ticks .k4::before { left: auto; right: 0; }
.claim-evidence { margin-top: 20px; }
.claim-evidence h3 { margin-bottom: 8px; }
figure { margin-top: 10px; border: 1px solid var(--ink); background: var(--wash); }
blockquote { padding: 12px 14px; font-size: .9375rem; line-height: 1.6; white-space: pre-wrap; }
figcaption { padding: 9px 14px 10px; border-top: 1px solid var(--ink); background: var(--sheet); color: var(--ink-3); font-size: .8125rem; }
figcaption a { font-weight: 650; }
figcaption p { margin-top: 3px; }
details { margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--hair); }
summary { display: flex; align-items: center; gap: 8px; width: fit-content; list-style: none; cursor: pointer;
  color: var(--blue); font-size: .875rem; font-weight: 650; }
summary::-webkit-details-marker { display: none; }
summary::before { content: ""; width: 6px; height: 6px; border: solid currentColor; border-width: 0 1.5px 1.5px 0; transform: rotate(-45deg); }
details[open] > summary::before { transform: rotate(45deg); }
details > :not(summary) { margin-top: 8px; }
pre { padding: 10px 12px; border: 1px solid var(--hair); background: var(--wash); white-space: pre-wrap; font: .8125rem/1.5 var(--mono); }
ul { padding-inline-start: 1.25rem; margin: 8px 0 0; }
.notes { padding: 0; margin: 0; list-style: none; counter-reset: note; }
.notes li { position: relative; padding: 9px 0 9px 36px; border-bottom: 1px solid var(--hair); color: var(--ink-2); font-size: .875rem; counter-increment: note; }
.notes li::before { content: counter(note); position: absolute; left: 0; top: 9px; display: grid; place-items: center; width: 22px; height: 22px;
  border: 1px solid var(--ink); border-radius: 50%; color: var(--ink); font: 700 .6875rem/1 var(--mono); }
@media (max-width: 720px) {
  .title-block { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .verdict-counts { grid-template-columns: 1fr; }
  .verdict-counts li + li { border-top: 1px solid var(--ink); border-left: 0; }
  .comparison-columns { grid-template-columns: 1fr; }
  .comparison-columns > div + div { border-top: 1px solid var(--ink); border-left: 0; }
}
@media (max-width: 540px) {
  main { margin: 0; padding: 24px 16px 30px; box-shadow: none; }
  main::before { inset: 6px; }
  .claim-card { margin-top: 22px; }
  .claim-card[data-verdict="POTENTIALLY_CHANGED"] { --r: 8px; padding: calc(var(--r) + 12px); }
  .answer-markup { padding: 12px 12px; }
  .claims-overview, .claims-overview caption, .claims-overview tbody { display: block; }
  .claims-overview thead { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
  .claims-overview tbody tr { display: grid; grid-template-columns: minmax(0, 1fr) auto; column-gap: 10px;
    padding: 9px 11px; margin-top: 8px; border: 1px solid var(--ink); }
  .claims-overview td { padding: 2px 0; border: 0; }
  .claims-overview td:nth-child(2) { grid-column: 1 / -1; }
  .claims-overview td:nth-child(3) { color: var(--ink-3); }
  .claims-overview td:nth-child(4) { justify-self: end; }
  .claims-overview td:last-child { grid-column: 2; grid-row: 1; padding-bottom: 14px; color: var(--ink-3); font-size: .8125rem; }
  .claims-overview td:last-child::before { content: "Freshness "; }
  .claims-overview td:last-child::after { right: 0; left: auto; bottom: 2px; }
}
@media print {
  html, body { background: #fff; }
  main { margin: 0; box-shadow: none; }
}
"""


_VERDICT_LABELS = {"MODEL_SUPPORTED": "Model-supported", "POTENTIALLY_CHANGED": "Potentially changed", "UNVERIFIABLE": "Unverifiable"}
_VERDICT_RANK = {"POTENTIALLY_CHANGED": 3, "UNVERIFIABLE": 2, "MODEL_SUPPORTED": 1}
_UNDERLINES = {"MODEL_SUPPORTED": "u-supported", "POTENTIALLY_CHANGED": "u-changed", "UNVERIFIABLE": "u-unverifiable"}
# Shape, not colour, carries the verdict: circle = model-supported, triangle = potentially changed, diamond = unverifiable.
_MARKS = {"MODEL_SUPPORTED": ("s-supported", '<circle cx="12" cy="12" r="10.5"/>', 16.2),
          "POTENTIALLY_CHANGED": ("s-changed", '<path d="M12 2 23 21.5H1Z"/>', 18.6),
          "UNVERIFIABLE": ("s-unverifiable", '<path d="M12 1 23 12 12 23 1 12Z"/>', 16.2)}
_USAGE_SCOPES = {"mock_observations_not_spending": "mock observations, not spending", "provider_reported_not_invoice": "provider-reported, not an invoice"}
_RULER_TICKS = ((365, "365 d"), (270, "270"), (180, "180"), (90, "90"), (0, "0 d"))
_BRAND = '<svg class="brand" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 3 21.5 20h-19Z"/><path d="m8.8 15.4 2.4 2.2 4-4.4"/></svg>'


def _mark(verdict, number=""):
    css, shape, baseline = _MARKS[verdict]
    label = f'<text x="12" y="{baseline}" text-anchor="middle" font-size="{11 if len(number) < 2 else 9}">{escape(number)}</text>' if number else ""
    return f'<svg class="shape {css}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">{shape}{label}</svg>'


def _marked_answer(report):
    """The answer with every selected claim underlined and a numbered callout after it; overlapping spans show the strongest verdict."""
    answer = report["answer_text"]
    spans = [(max(0, c["span"]["start"]), min(len(answer), c["span"]["end"]), c) for c in report["claims"]]
    spans = [span for span in spans if span[0] < span[1]]
    cuts = sorted({0, len(answer)} | {offset for begin, end, _ in spans for offset in (begin, end)})
    out = []
    for start, stop in zip(cuts, cuts[1:]):
        covering = [claim for begin, end, claim in spans if begin <= start and stop <= end]
        piece = escape(answer[start:stop])
        if covering:
            strongest = max(covering, key=lambda claim: _VERDICT_RANK[claim["verdict"]])
            piece = f'<mark class="{_UNDERLINES[strongest["verdict"]]}">{piece}</mark>'
        out.append(piece)
        for _, end, claim in spans:
            if end == stop:
                number = claim["id"].rpartition("-")[2]
                label = f'{claim["id"]}: {_VERDICT_LABELS[claim["verdict"]]}'
                out.append(f'<a class="callout" href="#{escape(claim["id"])}" aria-label="{escape(label)}">{_mark(claim["verdict"], number if number.isdigit() else "")}</a>')
    return ''.join(out)


def _ruler(fresh):
    """Decorative dimension line: days between the newest quoted source and the verification date (the sentence above it is authoritative)."""
    age = fresh["age_days"]
    scored = age is not None
    ticks = ''.join(f'<i class="k{index}" style="left:{(365 - days) / 3.65:.2f}%">{label}</i>' for index, (days, label) in enumerate(_RULER_TICKS))
    caption = "Age of the newest quoted source, in days before the verification date" if scored else "No quoted source has a usable date"
    return (f'<div class="ruler" data-scored="{"true" if scored else "false"}" style="--age:{min(age, 365) if scored else 365}" aria-hidden="true">'
            f'<span class="ruler-cap">{caption}</span><div class="ruler-track"><div class="ruler-dim"><b>{f"{age} d" if scored else "not dated"}</b></div></div>'
            f'<div class="ruler-ticks">{ticks}</div></div>')


def _title_block(report):
    usage = report["usage"]
    provenance, tokens, credits = usage["provenance"], usage["tokens"], usage["credits"]

    def cell(name, value, note=""):
        extra = f'<span class="note">{escape(note)}</span>' if note else ""
        return f'<div><dt>{name}</dt><dd>{value}{extra}</dd></div>'

    def basis(block):
        return f'{"complete" if block["complete"] else "incomplete"} · {_USAGE_SCOPES.get(block["scope"], block["scope"])}'

    return '<dl class="title-block">' + ''.join((
        cell("Verification date", f'<time>{escape(str(report["verification_date"]))}</time>'),
        cell("Answer date", escape(str(report["answer_date"] or "unknown"))),
        cell("Gateway", "Bifrost" if provenance["gateway"] == "bifrost" else "Mock"),
        cell("Search", "Tavily" if provenance["search"] == "tavily" else "Mock"),
        cell("Review", "Human review required"),
        cell("Model calls", escape(str(usage["gateway_calls"]))),
        cell("Search calls", escape(str(usage["search_calls"]))),
        cell("Tokens", escape(str(tokens["known_subtotal"]["total_tokens"])), basis(tokens)),
        cell("Search credits", escape(str(credits["known_subtotal"]["credits"])), basis(credits)),
        cell("Schema", escape(str(report["schema_version"]))),
    )) + '</dl>'


def render_html(report):
    """Render a script-free report with linked claims and expandable audit details."""
    e = lambda value: escape(str(value))
    mock = "mock" in report["usage"]["provenance"].values()
    verdict_labels = _VERDICT_LABELS
    parts = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">',
        f'<title>StaleCheck claim freshness report</title><style>{_REPORT_STYLE}</style></head><body><main>',
        '<header class="report-header">',
        f'<span class="report-mode">{_BRAND}StaleCheck{" · Mocked demo evidence" if mock else ""}</span>',
        '<h1>Claim freshness report</h1>',
        '<p class="report-review">Verdicts and proposed edits require human review. Freshness measures the recency of quoted evidence.</p>',
        _title_block(report), '</header>',
    ]

    if report["stop_reason"] == "approval_declined":
        parts.append('<p class="notice">Searches were declined. The report records the extracted claims without research evidence.</p>')

    if report["claims"]:
        counts = report["counts"]
        parts.append('<section class="sheet-section"><h2>At a glance</h2><ul class="verdict-counts">')
        for verdict in ("MODEL_SUPPORTED", "POTENTIALLY_CHANGED", "UNVERIFIABLE"):
            parts.append(f'<li>{_mark(verdict)}<strong>{e(counts[verdict])}</strong>{verdict_labels[verdict]}</li>')
        parts += [
            '</ul><table class="claims-overview" role="table"><caption>Selected claims and freshness scores out of 100</caption>',
            '<thead role="rowgroup"><tr role="row"><th scope="col">Claim</th><th scope="col">Excerpt</th><th scope="col">Category</th><th scope="col">Verdict</th><th scope="col">Freshness</th></tr></thead><tbody role="rowgroup">',
        ]
        for claim in report["claims"]:
            identifier = e(claim["id"])
            age = claim["freshness"]["age_days"]
            score = claim["freshness"]["score"]
            age_attr = "" if age is None else f' style="--age:{min(age, 365)}"'
            parts.append(
                f'<tr role="row" data-verdict="{e(claim["verdict"])}"{age_attr}>'
                f'<td role="cell"><a href="#{identifier}">{identifier}</a></td>'
                f'<td role="cell">{e(claim["text"])}</td><td role="cell">{e(claim["category"])}</td>'
                f'<td role="cell">{_mark(claim["verdict"])}{e(verdict_labels[claim["verdict"]])}</td><td role="cell">{e("not scored" if score is None else score)}</td></tr>'
            )
        parts += [
            '</tbody></table></section>',
            '<section class="sheet-section"><h2>Marked-up answer</h2>'
            '<p class="section-note">Each selected claim is marked where it appears in the answer: wavy red underline for potentially changed, dotted for unverifiable, '
            'solid for model-supported. A numbered mark follows each claim; select it to jump to the claim.</p>'
            f'<div class="answer-markup">{_marked_answer(report)}</div></section>',
            '<section class="sheet-section claims"><h2>Claims in detail</h2>',
        ]
    else:
        parts.append('<section class="sheet-section"><h2>No selected claims</h2><p>The assessment produced no claim verdicts. See the run details below.</p></section>')

    sources = {source["id"]: source for source in report["evidence"]}
    for claim in report["claims"]:
        inference = claim["inference"]
        fresh = claim["freshness"]
        scored = (f'score {fresh["score"]}/100, newest quoted source {fresh["newest_source_date"]}, {fresh["age_days"]} day(s) before the assessment date'
                  if fresh["score"] is not None else "not scored")
        verdict = claim["verdict"]
        score_block = (f'<strong>{fresh["score"]}</strong><span>/100</span><small>{e(fresh["state"])}</small>' if fresh["score"] is not None
                       else '<strong>–</strong><small>not scored</small>')
        parts += [
            f'<article class="claim-card" id="{e(claim["id"])}" data-verdict="{e(verdict)}"><header class="claim-head">'
            f'<h2>{_mark(verdict)}{e(claim["id"])} · {e(verdict_labels[verdict])}</h2><div class="score" aria-hidden="true">{score_block}</div></header>',
            '<div class="claim-comparison"><h3>Original excerpt and proposed edit</h3><div class="comparison-columns">',
            f'<div><strong>Original</strong><p>{e(claim["text"])}</p></div>',
            f'<div><strong>Model-generated proposal · Requires review</strong><p>{e(inference["suggested_rewrite"] or "No supported proposal retained")}</p></div></div></div>',
            f'<p class="claim-metadata">Category (model-labelled): {e(claim["category"])}. Freshness of the quoted evidence: {e(fresh["state"])} ({e(scored)}).</p>',
            _ruler(fresh),
            '<div class="claim-evidence"><h3>Verbatim source quotations</h3>',
        ]
        for quote in claim["evidence"]:
            source = sources[quote["source_id"]]
            parts.append(
                f'<figure><blockquote>{e(quote["quote"])}</blockquote><figcaption>'
                f'<a href="{e(quote["url"])}" target="_blank" rel="noopener noreferrer">{e(source["title"])}</a>'
                f'<p>Model-labelled relation: {e(quote["relation"])}. Retrieved: {e(source["retrieved_at"])}. '
                f'Estimated publication/update: {e(source["published_at"] or "unknown")}. Effective dates: unknown.</p></figcaption></figure>'
            )
        if not claim["evidence"]:
            parts.append('<p>No retained verbatim quotation.</p>')
        parts.append('</div>')
        retrieved = [source for source in report["evidence"] if source["claim_id"] == claim["id"]]
        if retrieved:
            parts.append('<details><summary>All retrieved sources</summary><p>Retrieved sources are not evidence unless quoted above.</p><ul>' + ''.join(
                f'<li><a href="{e(source["url"])}" target="_blank" rel="noopener noreferrer">{e(source["title"])}</a>. Estimated publication/update: {e(source["published_at"] or "unknown")}.</li>'
                for source in retrieved) + '</ul></details>')
        parts += [
            f'<details><summary>Model-generated reasoning</summary><p>{e(inference["observed"] or "No observation")}</p><p>{e(inference["rationale"] or "No rationale")}</p></details>',
            f'<details><summary>Claim audit details</summary><p>{e(claim["reason_code"])}. Answer span [{claim["span"]["start"]}, {claim["span"]["end"]}).</p>',
            f'<p>Query: {e(claim["query"])}. Time sensitivity (model-generated): {e(claim["time_sensitivity"]["level"])} · {e(claim["time_sensitivity"]["reason"])}</p>',
            f'<h3>Uncalibrated evidence-strength components</h3><pre>{e(json_bytes(claim["evidence_strength"]).decode())}</pre></details></article>',
        ]

    if report["claims"]:
        parts.append('</section>')
    parts += [
        f'<section class="sheet-section"><h2>Full original answer</h2><details><summary>View answer text</summary><pre>{e(report["answer_text"])}</pre></details></section>',
        f'<section class="sheet-section"><h2>Run details</h2><p>Stop reason: {e(report["stop_reason"] or "none")}.</p>',
        f'<details><summary>Exact research approval</summary><pre>{e(json_bytes(report["approval"]).decode())}</pre></details>',
        f'<details><summary>Rejected or unselected proposals</summary><pre>{e(json_bytes(report["rejected_proposals"]).decode())}</pre></details>',
        f'<details><summary>Usage and completeness</summary><pre>{e(json_bytes(report["usage"]).decode())}</pre></details></section>',
        '<section class="sheet-section"><h2>Limitations</h2><ol class="notes">' + ''.join(f'<li>{e(line)}</li>' for line in report['limitations']) + '</ol></section>',
        '</main></body></html>',
    ]
    return ''.join(parts)
