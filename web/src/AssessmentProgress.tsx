import { useEffect, useRef } from 'react';
import { isActive } from './assessment-api';
import type { ArtifactName, Job } from './assessment-api';

const stateLabels: Record<Job['state'], string> = {
  extracting: 'Identifying claims',
  awaiting_approval: 'Awaiting your approval',
  researching: 'Checking sources',
  cancel_requested: 'Cancellation requested',
  completed: 'Assessment completed',
  partial: 'Partial assessment',
  declined: 'Searches declined',
  expired: 'Assessment expired',
  cancelled: 'Assessment cancelled',
  failed: 'Assessment failed',
};

type Props = {
  job: Job;
  busy: boolean;
  pollPaused: boolean;
  decisionRecorded: boolean;
  onRefresh: () => void;
  onCancel: () => void;
  onDecide: (approved: boolean) => void;
  onShowReport: (button: HTMLButtonElement) => void;
  onDownload: (name: ArtifactName) => void;
  onReset: () => void;
};

export default function AssessmentProgress({
  job, busy, pollPaused, decisionRecorded, onRefresh, onCancel, onDecide, onShowReport, onDownload, onReset,
}: Props) {
  const heading = useRef<HTMLHeadingElement>(null);
  const approvalHeading = useRef<HTMLHeadingElement>(null);
  const active = isActive(job);
  const approval = job.state === 'awaiting_approval' ? job.approval : null;
  const approvalHash = approval?.sha256;

  useEffect(() => { heading.current?.focus(); }, [job.id]);
  useEffect(() => { if (approvalHash) approvalHeading.current?.focus(); }, [approvalHash]);

  return (
    <section className="panel assessment-progress" aria-labelledby="job-heading">
      <div className="panel-header">
        <h2 id="job-heading" ref={heading} tabIndex={-1}>{active ? 'Review and research' : 'Assessment results'}</h2>
        <p className="state-badge" data-state={job.state} role="status">{stateLabels[job.state]}</p>
      </div>

      <div className="panel-body progress-body">
        <div className="run-content">
          {active && !approval && <p className="result-intro">{
            job.state === 'extracting' ? 'Identifying time-sensitive claims in the answer.' :
            job.state === 'researching' ? 'Checking the approved sources for evidence.' :
            'Waiting for the assessment worker to stop.'
          }</p>}
          {approval && (
            <section className="assessment-approval" aria-labelledby="approval-heading">
              <h3 id="approval-heading" ref={approvalHeading} tabIndex={-1}>2. Review exact search request</h3>
              <p>Approve this exact set of queries and domains to start research. Review time counts toward the deadline.</p>
              <h4>Queries <span className="guidance">JSON strings</span></h4>
              <ol className="query-list">
                {approval.queries.map((query, index) => <li key={index}><pre>{JSON.stringify(query)}</pre></li>)}
              </ol>
              <h4>Primary domains</h4>
              {approval.primary_domains.length ? (
                <ul className="domain-list">{approval.primary_domains.map(domain => <li key={domain}><code>{JSON.stringify(domain)}</code></li>)}</ul>
              ) : <p>No primary domain restrictions.</p>}
              {decisionRecorded ? (
                <p className="approval-recorded" role="status">Decision recorded. Waiting for the service to continue.</p>
              ) : (
                <div className="actions approval-actions">
                  <button className="button primary" disabled={busy || pollPaused} onClick={() => onDecide(true)}>{job.mode === 'demo' ? 'Approve exact mocked searches' : 'Approve exact searches'}</button>
                  <button className="button secondary" disabled={busy || pollPaused} onClick={() => onDecide(false)}>Decline searches</button>
                </div>
              )}
              <details className="run-details">
                <summary>Request fingerprint</summary>
                <p className="mono">SHA-256 {approval.sha256}</p>
              </details>
            </section>
          )}

          {!active && (
            <p className="result-intro">
              {job.artifacts.length
                ? 'Review claim verdicts, source quotations and freshness scores in the report.'
                : 'This assessment ended without a report. The service retains any checkpoints recorded during the run.'}
            </p>
          )}

          {job.artifacts.length > 0 && (
            <section className="assessment-artifacts" aria-labelledby="artifacts-heading">
              <h3 id="artifacts-heading">Verified artifacts</h3>
              <p className="guidance">The service checks artifact integrity before each download. Claim verdicts still require review.</p>
              {job.artifacts.includes('report.html') && <button className="button primary" disabled={busy} onClick={event => onShowReport(event.currentTarget)}>Open verified report <span aria-hidden="true">↓</span></button>}
              <div className="artifact-downloads">
                {job.artifacts.map(name => <button className="button secondary" key={name} disabled={busy} onClick={() => onDownload(name)}>Download <code>{name}</code></button>)}
              </div>
            </section>
          )}
        </div>
        <aside className="run-sidebar" aria-label="Run status">
          <h3>Run status</h3>
          <dl className="run-metrics">
            {active && <div><dt>Time left at last update</dt><dd>{Math.ceil(job.remaining_seconds)} <span>sec</span></dd></div>}
            <div><dt>Provider attempts</dt><dd>{job.attempts}</dd></div>
          </dl>
          {active && (
            <div className="run-controls">
              {pollPaused && <p className="guidance">Status updates paused. Refresh before making a search decision.</p>}
              <div className="actions">
                <button className="button secondary" disabled={busy} onClick={onRefresh}>Refresh status</button>
                <button className="button secondary" disabled={busy || job.state === 'cancel_requested'} onClick={onCancel}>{job.state === 'cancel_requested' ? 'Cancellation requested' : 'Cancel assessment'}</button>
              </div>
              <p className="guidance">Cancellation waits for the worker to exit.{job.mode === 'live' && ' Requests already sent may finish and incur charges.'}</p>
            </div>
          )}
          <details className="run-details">
            <summary>Run details</summary>
            <dl className="metadata">
              <dt>Job ID</dt><dd><code>{job.id}</code></dd>
              <dt>State</dt><dd><code>{job.state}</code></dd>
              {job.stop_reason && <><dt>Stop reason</dt><dd><code>{job.stop_reason}</code></dd></>}
            </dl>
          </details>
        </aside>
      </div>
      {!active && <div className="panel-footer"><button className="button secondary" disabled={busy} onClick={onReset}>Prepare another assessment</button></div>}
    </section>
  );
}
