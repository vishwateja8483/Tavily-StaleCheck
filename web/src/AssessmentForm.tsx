import { useEffect } from 'react';
import type { FormEvent, RefObject } from 'react';
import { MAX_ANSWER_CODEPOINTS } from './assessment-api';
import type { AssessmentInput, Capabilities, InputErrors } from './assessment-api';

type Limit = 'max_claims' | 'max_attempts' | 'max_seconds';
export type Draft = Omit<AssessmentInput, 'consent' | 'primary_domains' | Limit> & Record<Limit, number | ''> & { domains: string };

type Props = {
  formRef: RefObject<HTMLFormElement | null>;
  draft: Draft;
  capabilities: Capabilities;
  locked: boolean;
  busy: boolean;
  demoLoaded: boolean;
  consent: boolean;
  inputErrors: InputErrors;
  onLoadDemo: () => void;
  onChange: <K extends keyof Draft>(key: K, value: Draft[K]) => void;
  onConsent: (checked: boolean) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
};

export default function AssessmentForm({
  formRef, draft, capabilities, locked, busy, demoLoaded, consent, inputErrors, onLoadDemo, onChange, onConsent, onSubmit,
}: Props) {
  const fixedDemo = capabilities.mode === 'demo';
  const answerCount = Array.from(draft.answer_text).length;
  const invalidFields = Object.keys(inputErrors);

  useEffect(() => {
    const firstInvalid = Object.keys(inputErrors)[0];
    const field = firstInvalid && formRef.current?.querySelector<HTMLElement>(`[name="${firstInvalid}"]`);
    if (field) {
      const disclosure = field.closest('details');
      if (disclosure) disclosure.open = true;
      field.focus();
    }
  }, [formRef, inputErrors]);

  function fieldProps(name: keyof AssessmentInput, guidance?: string) {
    return {
      name,
      id: `assessment-${name}`,
      'aria-invalid': !!inputErrors[name],
      'aria-describedby': [guidance, inputErrors[name] ? `error-${name}` : null].filter(Boolean).join(' ') || undefined,
    };
  }
  const fieldError = (name: keyof AssessmentInput) => inputErrors[name] && <p className="field-error" id={`error-${name}`}>{inputErrors[name]}</p>;
  const readLimit = (input: HTMLInputElement) => input.value === '' ? '' : input.valueAsNumber;

  return (
    <form ref={formRef} noValidate className="panel assessment-form" onSubmit={onSubmit} aria-labelledby="input-heading">
      <div className="panel-header">
        <h2 id="input-heading" tabIndex={-1}>Answer and scope</h2>
        {fixedDemo && <button className="button secondary" disabled={locked} type="button" onClick={onLoadDemo} title="Load the four-claim Acme sample">{demoLoaded ? 'Reload sample' : 'Load Acme example'}</button>}
      </div>
      {invalidFields.length > 0 && <p className="notice danger form-error" role="alert">Correct the highlighted fields. No request was sent.</p>}
      <fieldset disabled={locked || (!fixedDemo && !capabilities.configured)}>
        <div className="panel-body form-body">
          <div className="field answer-editor">
            <label htmlFor="assessment-answer_text">Full answer</label>
            <textarea {...fieldProps('answer_text', 'answer-guidance')} required readOnly={fixedDemo} rows={9} value={draft.answer_text} onChange={event => onChange('answer_text', event.target.value)} placeholder={fixedDemo ? 'Load the Acme sample to begin.' : 'Paste the AI-generated answer you want to check.'} autoComplete="off" spellCheck={false} />
            <p className="guidance" id="answer-guidance" data-over-limit={answerCount > MAX_ANSWER_CODEPOINTS}>{answerCount.toLocaleString('en-US')} / {MAX_ANSWER_CODEPOINTS.toLocaleString('en-US')} Unicode code points</p>
            {fieldError('answer_text')}
          </div>
          <section className="scope" aria-labelledby="scope-heading">
            <h3 id="scope-heading">Verification scope</h3>
            <div className="field-group date-fields">
              <div className="field">
                <label htmlFor="assessment-verification_date">Verification date</label>
                <input {...fieldProps('verification_date')} type="date" required readOnly={fixedDemo} min="0001-01-01" max="9999-12-31" value={draft.verification_date} onChange={event => onChange('verification_date', event.target.value)} />
                {fieldError('verification_date')}
              </div>
              <div className="field">
                <label htmlFor="assessment-answer_date">Answer date <span>(optional)</span></label>
                <input {...fieldProps('answer_date', 'date-guidance')} type="date" readOnly={fixedDemo} min="0001-01-01" max={draft.verification_date || '9999-12-31'} value={draft.answer_date ?? ''} onChange={event => onChange('answer_date', event.target.value || null)} />
                {fieldError('answer_date')}
              </div>
              <p className="guidance field-group-note" id="date-guidance">Use the date the answer was written, if known.</p>
            </div>
            <div className="field">
              <label htmlFor="assessment-primary_domains">Primary domains <span>(one per line, at least one required)</span></label>
              <textarea {...fieldProps('primary_domains', 'domains-guidance')} required readOnly={fixedDemo} rows={2} placeholder="docs.example.org" value={draft.domains} onChange={event => onChange('domains', event.target.value)} autoComplete="off" spellCheck={false} />
              <p className="guidance" id="domains-guidance">Search only these sources. Enter up to 10 distinct public hostnames without URLs or paths.</p>
              {fieldError('primary_domains')}
            </div>
            <details className="budget">
              <summary>Run limits</summary>
              <div className="field-group budget-fields">
                <div className="field">
                  <label htmlFor="assessment-max_claims">Maximum claims</label>
                  <input {...fieldProps('max_claims', 'budget-guidance')} type="number" min={1} max={8} step={1} required value={draft.max_claims} onChange={event => onChange('max_claims', readLimit(event.target))} />
                  {fieldError('max_claims')}
                </div>
                <div className="field">
                  <label htmlFor="assessment-max_attempts">Maximum attempts</label>
                  <input {...fieldProps('max_attempts', 'budget-guidance')} type="number" min={1} max={25} step={1} required value={draft.max_attempts} onChange={event => onChange('max_attempts', readLimit(event.target))} />
                  {fieldError('max_attempts')}
                </div>
                <div className="field">
                  <label htmlFor="assessment-max_seconds">Deadline <span>(seconds, includes review)</span></label>
                  <input {...fieldProps('max_seconds', 'budget-guidance')} type="number" min={0} max={180} step="any" required value={draft.max_seconds} onChange={event => onChange('max_seconds', readLimit(event.target))} />
                  {fieldError('max_seconds')}
                </div>
              </div>
              <p className="guidance" id="budget-guidance">Allow one attempt to identify claims and up to two per claim to search and assess evidence. Review time counts toward the deadline.</p>
            </details>
          </section>
        </div>

        <div className="panel-footer form-footer">
          <label className="consent">
            <input type="checkbox" disabled={fixedDemo && !demoLoaded} checked={consent} onChange={event => onConsent(event.target.checked)} />
            <span><strong>Consent to answer processing</strong>{fixedDemo ? 'I agree to process this answer with mocked providers. The demo sends no external requests.' : 'I agree to send the full answer to Bifrost. Selected claims and excerpts may follow. Tavily receives the queries and domains you approve. Provider requests may be billed.'}</span>
          </label>
          <button className="button primary" disabled={!consent || (fixedDemo && !demoLoaded)} type="submit">{busy ? 'Starting assessment…' : fixedDemo ? 'Start mocked assessment' : 'Start live assessment'} <span aria-hidden="true">→</span></button>
        </div>
      </fieldset>
    </form>
  );
}
