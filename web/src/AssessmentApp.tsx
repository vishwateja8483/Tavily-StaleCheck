import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import AssessmentForm from './AssessmentForm';
import type { Draft } from './AssessmentForm';
import AssessmentProgress from './AssessmentProgress';
import AssessmentRoute from './AssessmentRoute';
import { BrandMark, VerdictMark } from './marks';
import { AssessmentInputError, AssessmentRequestError, configurationKeys, isActive, validateInput } from './assessment-api';
import type { ArtifactName, AssessmentApi, AssessmentInput, Capabilities, InputErrors, Job } from './assessment-api';

function initialDraft(): Draft {
  const now = new Date();
  const localDate = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return {
    answer_text: '', verification_date: localDate.toISOString().slice(0, 10), answer_date: null,
    domains: '', max_claims: 4, max_attempts: 9, max_seconds: 180,
  };
}
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
  const reportHeading = useRef<HTMLHeadingElement>(null);
  const openReportButton = useRef<HTMLButtonElement>(null);
  const focusSetup = useRef(false);

  useEffect(() => {
    if (report !== null) reportHeading.current?.focus();
  }, [report]);

  useEffect(() => {
    if (!job && focusSetup.current) {
      focusSetup.current = false;
      formRef.current?.querySelector<HTMLElement>('#input-heading')?.focus();
    }
  }, [job]);
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
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!api || !consent || busyRef.current || pending || job || !capabilities || (capabilities.mode === 'demo' && !demoLoaded) || (capabilities.mode === 'live' && !capabilities.configured)) return;
    let input: AssessmentInput;
    try {
      const { domains, ...fields } = draft;
      input = validateInput({ ...fields, primary_domains: domains.split(/\r?\n/).map(domain => domain.trim().toLowerCase()).filter(Boolean), consent: true });
    } catch (reason) {
      if (reason instanceof AssessmentInputError) {
        setInputErrors(reason.fields); setError(null);
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
  function showReport(button: HTMLButtonElement) {
    if (!api || !job) return;
    if (report !== null) {
      reportHeading.current?.focus();
      return;
    }
    openReportButton.current = button;
    void perform(async () => {
      const blob = await api.artifact(job.id, 'report.html');
      const html = await blob.text();
      // Relative fragments in srcdoc otherwise resolve against the parent page.
      const embedded = html.replace(/^<!doctype html>/i, '').replace(/href="(#claim-\d+)"/g, 'href="about:srcdoc$1"');
      setReport('<!doctype html>' + framePolicy + embedded);
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
  function resetAssessment() {
    generation.current += 1;
    focusSetup.current = true;
    setJob(null);
    setReport(null);
    openReportButton.current = null;
    setDecided(null);
    setConsent(false);
    setPollPaused(false);
    setError(null);
    if (capabilities?.mode === 'demo') {
      setDraft(initialDraft());
      setDemoLoaded(false);
    }
  }

  const demo = capabilities?.mode === 'demo';
  return (
    <div className="sheet">
      <a className="skip-link" href="#assessment-main">Skip to assessment</a>
      <header className="topbar">
        <div className="brand">
          <BrandMark />
          <span className="brand-name">StaleCheck</span>
          <span className="brand-tagline">Stale-answer detector</span>
        </div>
        <p className="mode-cell" data-mode={capabilities?.mode}>{capabilities ? (demo ? 'Demo · mocked providers' : 'Live · Bifrost + Tavily') : 'Local workspace'}</p>
      </header>
      <main className="workspace" id="assessment-main" tabIndex={-1}>
        <div className="title-row">
          <div className="title-text">
            <h1>{job ? 'Assessment' : 'New assessment'}</h1>
            {!job && <p>Check an AI answer against current sources.</p>}
          </div>
          <ul className="legend" role="list" aria-label="Verdict marks">
            <li><VerdictMark kind="supported" />Model-supported</li>
            <li><VerdictMark kind="changed" />Potentially changed</li>
            <li><VerdictMark kind="unverifiable" />Unverifiable</li>
          </ul>
        </div>
        <AssessmentRoute job={job} demo={demo} />

        {!api && (
          <div className="notice danger" role="alert">
            <strong>Launch token missing or invalid.</strong>
            <p>Open the private launch URL printed by the local service. The token is kept in page memory, so reloading loses access.</p>
          </div>
        )}
        {error && <div className="notice danger" role="alert">{error}</div>}

        <div className="workspace-main">
          {api && !capabilities && (
            <section className="panel panel-body">
              <h2>{error ? 'Service unavailable' : 'Connecting to the local service'}</h2>
              {error
                ? <button className="button secondary" disabled={busy} onClick={() => void perform(async () => setCapabilities(await api.capabilities()))}>Retry capabilities</button>
                : <p role="status">Loading assessment settings…</p>}
            </section>
          )}

          {pending && !busy && (
            <section className="notice caution" aria-labelledby="pending-heading">
              <h2 id="pending-heading">Creation outcome uncertain</h2>
              <p>Keep this page open. Retrying uses the original input and request key, so the service can return the existing assessment if it started one.</p>
              <button className="button secondary" onClick={retryCreation}>Retry identical creation request</button>
            </section>
          )}

          {capabilities?.mode === 'live' && !capabilities.configured && <p className="notice caution">Live creation disabled. Configure the local service and relaunch.</p>}
          {capabilities && !job && (
            <AssessmentForm formRef={formRef} draft={draft} capabilities={capabilities} locked={busy || !!pending} busy={busy}
              demoLoaded={demoLoaded} consent={consent} inputErrors={inputErrors} onLoadDemo={loadDemo}
              onChange={updateDraft} onConsent={setConsent} onSubmit={submit} />
          )}
          {job && (
            <AssessmentProgress job={job} busy={busy} pollPaused={pollPaused}
              decisionRecorded={decided === `${job.id}:${job.approval?.sha256}`}
              onRefresh={refresh} onCancel={cancel} onDecide={decide} onShowReport={showReport}
              onDownload={download} onReset={resetAssessment} />
          )}
        </div>

        {report !== null && (
          <section className="panel report-panel" aria-labelledby="report-heading">
            <div className="panel-header">
              <div>
                <h2 id="report-heading" ref={reportHeading} tabIndex={-1}>Server-verified report</h2>
                <p>{demo ? 'Mocked demo evidence. ' : ''}Source links open in a new tab.</p>
              </div>
              <button className="button secondary" onClick={() => { setReport(null); openReportButton.current?.focus(); }}>Close report</button>
            </div>
            <iframe className="assessment-report" title="Verified assessment report" sandbox="allow-popups allow-popups-to-escape-sandbox" referrerPolicy="no-referrer" srcDoc={report} />
          </section>
        )}

        <footer className="assessment-footer">
          {capabilities && (
            <section className="service-configuration" aria-labelledby="provider-heading">
              <h2 id="provider-heading">{demo ? 'Demo mode — mocked providers' : 'Bifrost and Tavily'}</h2>
              <details>
                <summary>Service configuration</summary>
                <p>{demo ? 'The sample uses invented evidence and runs offline.' : `Bifrost handles model requests. Tavily searches the approved domains${capabilities.configuration.BIFROST_SEARCH_RELAY ? ' through the Bifrost relay' : ''}.`}</p>
                <p>Configuration presence is not connectivity or a successful credential check. This launch allows {capabilities.max_sessions} sessions.</p>
                <ul className="configuration">
                  {configurationKeys.map(key => <li key={key}><code>{key}</code><span>{capabilities.configuration[key] ? 'Present' : 'Absent'}</span></li>)}
                </ul>
              </details>
            </section>
          )}
          <p>Answers and launch tokens stay out of browser storage.</p>
        </footer>
      </main>
    </div>
  );
}
