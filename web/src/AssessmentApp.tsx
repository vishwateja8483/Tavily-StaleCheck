import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { AssessmentInputError, AssessmentRequestError, MAX_ANSWER_CODEPOINTS, configurationKeys, isActive, validateInput } from './assessment-api';
import type { ArtifactName, AssessmentApi, AssessmentInput, Capabilities, InputErrors, Job } from './assessment-api';

type Draft = Omit<AssessmentInput, 'consent' | 'primary_domains'> & { domains: string };
const initialDraft = (): Draft => ({ answer_text: '', verification_date: new Date().toISOString().slice(0, 10), answer_date: null, domains: '', max_claims: 4, max_attempts: 8, max_seconds: 180 });
const errorText = (error: unknown) => error instanceof Error ? error.message : 'Assessment request failed. No action was retried.';
const framePolicy = '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:; form-action \'none\'; base-uri \'none\'">';

export default function AssessmentApp({ api }: { api: AssessmentApi | null }) {
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [draft, setDraft] = useState<Draft>(initialDraft);
  const [consent, setConsent] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [pending, setPending] = useState<{ key: string; input: AssessmentInput } | null>(null);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const [inputErrors, setInputErrors] = useState<InputErrors>({});
  const formRef = useRef<HTMLFormElement>(null);
  const [demoLoaded, setDemoLoaded] = useState(false);
  const [pollPaused, setPollPaused] = useState(false);
  const [report, setReport] = useState<string | null>(null);
  // Job and approval SHA-256 already answered; identical demo requests share a hash across jobs.
  const [decided, setDecided] = useState<string | null>(null);
  // Invalidates in-flight status reads before any mutation, preventing stale approval UI.
  const generation = useRef(0);

  useEffect(() => {
    if (!api) return;
    let disposed = false;
    api.capabilities().then(value => { if (!disposed) setCapabilities(value); }).catch(reason => { if (!disposed) setError(errorText(reason)); });
    return () => { disposed = true; };
  }, [api]);

  useEffect(() => {
    if (!api || !job || !isActive(job) || busy || pollPaused) return;
    let disposed = false;
    const version = generation.current;
    const timer = window.setTimeout(() => {
      api.job(job.id).then(value => {
        if (!disposed && version === generation.current) setJob(value);
      }).catch(reason => {
        if (!disposed && version === generation.current) { setError(errorText(reason)); setPollPaused(true); }
      });
    }, 1000);
    return () => { disposed = true; window.clearTimeout(timer); };
  }, [api, job, busy, pollPaused]);

  async function perform(action: () => Promise<void>) {
    if (busyRef.current) return;
    busyRef.current = true;
    generation.current += 1;
    setBusy(true); setError(null);
    try { await action(); }
    catch (reason) { setError(errorText(reason)); setPollPaused(true); }
    finally { busyRef.current = false; setBusy(false); }
  }
  function updateDraft<K extends keyof Draft>(key: K, value: Draft[K]) {
    if (busyRef.current || pending || job || (capabilities?.mode === 'demo' && ['answer_text', 'verification_date', 'answer_date', 'domains'].includes(key))) return;
    setDraft(previous => ({ ...previous, [key]: value })); setConsent(false); setInputErrors({});
  }
  function loadDemo() {
    const input = capabilities?.demo_input;
    if (!input || busyRef.current || pending || job) return;
    const { consent: _consent, primary_domains, ...fields } = input;
    setDraft({ ...fields, domains: primary_domains.join('\n') }); setConsent(false); setInputErrors({}); setDemoLoaded(true);
  }
  async function createAttempt(attempt: { key: string; input: AssessmentInput }) {
    if (!api) return;
    try {
      const created = await api.create(attempt.key, attempt.input);
      setJob(created); setPending(null); setPollPaused(false);
    } catch (reason) {
      // The service answers every 4xx before reserving a job (invalid input, busy slot,
      // capacity), so the form unlocks. Transport failures and 5xx retain the original key.
      if (reason instanceof AssessmentRequestError && reason.status >= 400 && reason.status < 500) {
        setPending(null); setConsent(false);
      }
      throw reason;
    }
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!api || !consent || busyRef.current || pending || job || !capabilities || (capabilities.mode === 'demo' && !demoLoaded) || (capabilities.mode === 'live' && !capabilities.configured)) return;
    let input: AssessmentInput;
    try {
      const { domains, ...fields } = draft;
      input = validateInput({ ...fields, primary_domains: domains.split(/\r?\n/).map(domain => domain.trim().toLowerCase()).filter(Boolean), consent: true });
    } catch (reason) {
      if (reason instanceof AssessmentInputError) {
        setInputErrors(reason.fields); setError(null);
        const first = Object.keys(reason.fields)[0];
        formRef.current?.querySelector<HTMLElement>(`[name="${first}"]`)?.focus();
      }
      return;
    }
    setInputErrors({});
    void perform(async () => {
      const attempt = { key: crypto.randomUUID(), input };
      setPending(attempt);
      await createAttempt(attempt);
    });
  }
  function retryCreation() {
    if (!api || !pending) return;
    void perform(async () => {
      // User-initiated only: preserve the key unless the server rejects the input.
      await createAttempt(pending);
    });
  }
  function refresh() {
    if (!api || !job) return;
    void perform(async () => { setJob(await api.job(job.id)); setPollPaused(false); });
  }
  function decide(approved: boolean) {
    if (!api || !job?.approval) return;
    const request = job.approval;
    void perform(async () => { const updated = await api.approve(job.id, request, approved); setDecided(`${job.id}:${request.sha256}`); setJob(updated); setPollPaused(false); });
  }
  function cancel() {
    if (!api || !job) return;
    void perform(async () => { setJob(await api.cancel(job.id)); setPollPaused(false); });
  }
  function showReport() {
    if (!api || !job) return;
    setReport(null);
    void perform(async () => {
      const blob = await api.artifact(job.id, 'report.html');
      setReport(framePolicy + await blob.text());
    });
  }
  function download(name: ArtifactName) {
    if (!api || !job) return;
    void perform(async () => {
      const blob = await api.artifact(job.id, name);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url; anchor.download = name; anchor.rel = 'noopener';
      document.body.append(anchor); anchor.click(); anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }
  const locked = busy || !!pending || !!job;
  const fixedDemo = capabilities?.mode === 'demo';
  const answerCount = Array.from(draft.answer_text).length;
  const canCreate = !!api && !!capabilities && (capabilities.mode === 'demo' || capabilities.configured);
  function fieldProps(name: keyof AssessmentInput, guidance?: string) {
    return { name, id: `assessment-${name}`, 'aria-invalid': !!inputErrors[name], 'aria-describedby': [guidance, inputErrors[name] ? `error-${name}` : null].filter(Boolean).join(' ') || undefined };
  }
  const fieldError = (name: keyof AssessmentInput) => inputErrors[name] && <p className="assessment-field-error" id={`error-${name}`}>{inputErrors[name]}</p>;

  return <>
    <a className="skip-link" href="#assessment-main">Skip to assessment</a>
    <header className="topbar"><div className="topbar-inner"><div className="brand">StaleCheck<span className="brand-sub">Local assessment</span></div><span className="badge">Single user · loopback only</span></div></header>
    <main className="workspace assessment" id="assessment-main">
      <section className="hero"><div><span className="eyebrow">An explicit, bounded assessment</span><h1>Review before research.</h1><p className="hero-copy">Submit an answer, review the exact proposed searches, then inspect the evidence. Completion is not proof of truth; assessment state is not a report verdict.</p></div></section>
      <div className="scope-banner"><div><strong>Your approval has two steps.</strong><p>First consent to the full answer being processed. Then approve or decline the literal search queries and domains before research begins. Review time counts toward the original deadline.</p></div></div>
      {!api && <div className="error-banner" role="alert">Launch token missing or invalid. Reopen the local service launch URL. The token is held only in page memory; reloading loses access.</div>}
      {error && <div className="error-banner" role="alert">{error}<p>No automatic retry. A failed request may already have taken effect.</p></div>}
      {api && !capabilities && <section className="panel assessment-section"><p>{error ? 'Capabilities unavailable.' : 'Loading local capabilities…'}</p>{error && <button className="button" disabled={busy} onClick={() => void perform(async () => setCapabilities(await api.capabilities()))}>Retry capabilities</button>}</section>}
      {capabilities && <section className="panel assessment-section" aria-labelledby="connection-heading">
        <h2 id="connection-heading">{capabilities.mode === 'demo' ? 'Demo mode — mocked providers' : 'Live mode — external providers'}</h2>
        <p>{capabilities.mode === 'demo' ? 'Mock extraction and search only. No provider calls; invented demo evidence does not establish live compatibility.' : `The full answer is sent to Bifrost; selected claims and source excerpts may follow. Only your approved exact queries are sent to Tavily${capabilities.configuration.BIFROST_SEARCH_RELAY ? ', relayed by Bifrost' : ''}. Provider requests may be billed.`}</p>
        <p className="muted">Configuration presence is not connectivity or a successful credential check. Searches need TAVILY_API_KEY, or BIFROST_SEARCH_RELAY to route them through Bifrost. Session capacity for this launch: {capabilities.max_sessions}; restarting never resumes unfinished work.</p>
        <ul className="assessment-config">{configurationKeys.map(key => <li key={key}><code>{key}</code> — {capabilities.configuration[key] ? 'present' : 'absent'}</li>)}</ul>
        {capabilities.mode === 'live' && !capabilities.configured && <p className="warning-text">Live creation disabled by the service configuration. Configure the server and relaunch; no secrets are entered here.</p>}
      </section>}
      <form ref={formRef} noValidate className="panel assessment-section" onSubmit={submit} aria-labelledby="input-heading">
        <h2 id="input-heading">1. Consent to answer processing</h2>
        {Object.keys(inputErrors).length > 0 && <div className="error-banner" role="alert">Correct the highlighted fields. No request was sent; no assessment was started.</div>}
        <fieldset disabled={locked || !canCreate}>
          {fixedDemo && capabilities?.demo_input && <><button className="button" type="button" onClick={loadDemo}>Use service-provided demo input</button><p className="muted" id="demo-guidance">Load the service-provided demo explicitly. Its answer, dates and domains are read-only; budget limits remain editable. Loading or changing inputs resets consent.</p></>}
          <label htmlFor="assessment-answer_text">Full answer</label><textarea {...fieldProps('answer_text', 'answer-guidance')} required readOnly={fixedDemo} rows={8} value={draft.answer_text} onChange={event => updateDraft('answer_text', event.target.value)} autoComplete="off" spellCheck={false} />
          <p className="assessment-guidance" id="answer-guidance">{answerCount.toLocaleString('en-US')} / {MAX_ANSWER_CODEPOINTS.toLocaleString('en-US')} Unicode code points. A nonblank answer is required; nothing is truncated automatically.</p>{fieldError('answer_text')}
          <div className="assessment-fields"><div><label htmlFor="assessment-verification_date">Verification date</label><input {...fieldProps('verification_date', 'date-guidance')} type="date" required readOnly={fixedDemo} min="0001-01-01" max="9999-12-31" value={draft.verification_date} onChange={event => updateDraft('verification_date', event.target.value)} />{fieldError('verification_date')}</div>
            <div><label htmlFor="assessment-answer_date">Answer date (optional)</label><input {...fieldProps('answer_date', 'date-guidance')} type="date" readOnly={fixedDemo} min="0001-01-01" max={draft.verification_date || '9999-12-31'} value={draft.answer_date ?? ''} onChange={event => updateDraft('answer_date', event.target.value || null)} />{fieldError('answer_date')}</div></div>
          <p className="assessment-guidance" id="date-guidance">Use valid calendar dates in years 0001–9999. Answer date must not follow verification date.</p>
          <label htmlFor="assessment-primary_domains">Primary domains (one per line, at least one required)</label><textarea {...fieldProps('primary_domains', 'domains-guidance')} required readOnly={fixedDemo} rows={3} placeholder="example.org" value={draft.domains} onChange={event => updateDraft('domains', event.target.value)} autoComplete="off" spellCheck={false} />
          <p className="assessment-guidance" id="domains-guidance">Enter 1–10 distinct public ASCII hostnames, such as example.org (labels up to 63 characters). No URLs, ports, paths, wildcards, IP addresses or private/reserved suffixes. Surrounding whitespace and case are normalized; duplicates are not removed. The server makes the final validation decision.</p>{fieldError('primary_domains')}
          <div className="assessment-fields"><div><label htmlFor="assessment-max_claims">Maximum claims</label><input {...fieldProps('max_claims', 'budget-guidance')} type="number" min={1} max={8} step={1} required value={draft.max_claims} onChange={event => updateDraft('max_claims', Number(event.target.value))} />{fieldError('max_claims')}</div>
            <div><label htmlFor="assessment-max_attempts">Maximum attempts</label><input {...fieldProps('max_attempts', 'budget-guidance')} type="number" min={1} max={25} step={1} required value={draft.max_attempts} onChange={event => updateDraft('max_attempts', Number(event.target.value))} />{fieldError('max_attempts')}</div>
            <div><label htmlFor="assessment-max_seconds">Deadline (seconds, includes review)</label><input {...fieldProps('max_seconds', 'budget-guidance')} type="number" min={0} max={180} step="any" required value={draft.max_seconds} onChange={event => updateDraft('max_seconds', Number(event.target.value))} />{fieldError('max_seconds')}</div></div>
          <p className="assessment-guidance" id="budget-guidance">Claims: whole number 1–8. Attempts: whole number 1–25. Deadline: greater than 0 and at most 180 seconds, including review.</p>
          <label className="assessment-consent"><input type="checkbox" disabled={fixedDemo && !demoLoaded} checked={consent} onChange={event => setConsent(event.target.checked)} />{fixedDemo ? 'I consent to process this full answer with mocked providers for this demo. No live requests will be sent.' : 'I consent to send this full answer to Bifrost, followed by selected claims and source excerpts. I understand exact approved queries will go to Tavily and requests may be billed.'}</label>
          <button className="button primary" disabled={!consent || (fixedDemo && !demoLoaded)} type="submit">{fixedDemo ? 'Start mocked assessment' : 'Start live assessment'}</button>
        </fieldset>
      </form>
      {pending && <section className="panel assessment-section"><h2>Creation outcome uncertain</h2><p>The original input and idempotency key remain in memory. Do not reload or create a different job. An explicit retry below sends the identical request, not a new assessment. If creation was rejected, correct the service issue before retrying.</p><button className="button" disabled={busy} onClick={retryCreation}>Retry identical creation request</button></section>}
      {job && <section className="panel assessment-section" aria-labelledby="job-heading">
        <h2 id="job-heading">Assessment progress {job.mode === 'demo' && <span className="badge warning">Mocked demo</span>}</h2>
        <p role="status">Service state: <strong>{job.state}</strong> · {Math.ceil(job.remaining_seconds)} seconds remaining at last update · {job.attempts} attempts</p>
        <p className="mono small">Job {job.id}</p>
        {job.stop_reason && <p>Stop reason: {job.stop_reason}</p>}
        {isActive(job) && <><p className="muted">{pollPaused ? 'Polling paused after an error. Refresh explicitly to reconcile the current state.' : 'Status is polled once per second while active; failed requests stop polling.'} Cancellation is cooperative: requests already sent can finish and be billed. The active slot stays occupied until the worker exits.</p><div className="assessment-actions"><button className="button" disabled={busy} onClick={refresh}>Refresh status</button><button className="button" disabled={busy || job.state === 'cancel_requested'} onClick={cancel}>{job.state === 'cancel_requested' ? 'Cancellation requested' : 'Cancel assessment'}</button></div></>}
        {job.state === 'awaiting_approval' && job.approval && <div className="assessment-approval"><h2>2. Review exact search request</h2><p>Approval applies only to the complete request below, without edits. Declining sends no searches. The original deadline keeps running while you review.</p>
          <h3>Literal queries (JSON strings preserve whitespace)</h3><ol>{job.approval.queries.map((query, index) => <li key={index}><pre>{JSON.stringify(query)}</pre></li>)}</ol>
          <h3>Literal primary domains</h3>{job.approval.primary_domains.length ? <ul>{job.approval.primary_domains.map((domain, index) => <li key={index}><pre>{JSON.stringify(domain)}</pre></li>)}</ul> : <p>No primary domain restrictions.</p>}
          <p className="mono small assessment-hash">Request SHA-256: {job.approval.sha256}</p>
          {decided === `${job.id}:${job.approval.sha256}` ? <p role="status">Decision recorded. Waiting for the service to continue.</p> : <div className="assessment-actions"><button className="button primary" disabled={busy || pollPaused} onClick={() => decide(true)}>{job.mode === 'demo' ? 'Approve exact mocked searches' : 'Approve exact searches'}</button><button className="button" disabled={busy || pollPaused} onClick={() => decide(false)}>Decline searches</button></div>}
        </div>}
        {!isActive(job) && <p>{['failed', 'cancelled', 'expired'].includes(job.state) ? 'Interrupted work preserves server checkpoints, not a completed report.' : 'Collection has stopped. A completed or partial collection is not proof of truth. Inspect the report verdicts and limitations.'}</p>}
        {job.artifacts.length > 0 && <div className="assessment-artifacts"><h2>Verified artifacts</h2><p>The server verifies artifacts afresh before each authenticated fetch. This verifies artifact integrity, not the truth of claims.</p><div className="assessment-actions">{job.artifacts.includes('report.html') && <button className="button primary" disabled={busy} onClick={showReport}>Open verified report</button>}{job.artifacts.map(name => <button className="button" key={name} disabled={busy} onClick={() => download(name)}>Download {name}</button>)}</div></div>}
        {!isActive(job) && <button className="button" disabled={busy} onClick={() => { generation.current += 1; setJob(null); setReport(null); setDecided(null); setConsent(false); setPollPaused(false); setError(null); }}>Prepare another assessment</button>}
      </section>}
      {report !== null && <section className="panel assessment-section"><h2>Server-verified report {job?.mode === 'demo' && '— mocked demo evidence'}</h2><p className="muted">Isolated, script-free report. No script or same-origin permissions are granted; source links open in a new tab.</p><iframe className="assessment-report" title="Verified assessment report" sandbox="allow-popups allow-popups-to-escape-sandbox" referrerPolicy="no-referrer" srcDoc={report} /></section>}
      <footer className="assessment-footer muted">Token and answer stay out of browser storage. Reload requires the original launch URL. Local service state does not resume after restart.</footer>
    </main>
  </>;
}
