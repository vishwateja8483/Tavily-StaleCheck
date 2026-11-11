import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import AssessmentApp from './AssessmentApp';
import AssessmentRoute from './AssessmentRoute';
import { AssessmentInputError, AssessmentRequestError, consumeLaunchToken, createAssessmentApi, validateCapabilities, validateInput, validateJob } from './assessment-api';
import type { AssessmentApi, AssessmentInput, Capabilities, Job } from './assessment-api';

const input: AssessmentInput = { answer_text: 'Mock answer', verification_date: '2026-11-02', answer_date: null, primary_domains: ['example.org'], max_claims: 4, max_attempts: 9, max_seconds: 180, consent: true };
const capabilities: Capabilities = { mode: 'demo', configured: false, configuration: { BIFROST_URL: false, BIFROST_MODEL: false, BIFROST_VIRTUAL_KEY: false, TAVILY_API_KEY: false, BIFROST_SEARCH_RELAY: false }, max_sessions: 4, demo_input: input };
const job: Job = { id: 'job-1', mode: 'demo', state: 'awaiting_approval', remaining_seconds: 120, approval: { queries: ['  literal "query"\n<script>hostile</script>'], primary_domains: ['example.org'], sha256: 'a'.repeat(64) }, stop_reason: null, attempts: 0, artifacts: [] };
function mockApi(): AssessmentApi {
  return { capabilities: vi.fn().mockResolvedValue(capabilities), create: vi.fn().mockResolvedValue(job), job: vi.fn().mockResolvedValue(job), approve: vi.fn().mockResolvedValue({ ...job, state: 'researching', approval: null }), cancel: vi.fn().mockResolvedValue({ ...job, state: 'cancel_requested', approval: null }), artifact: vi.fn().mockResolvedValue(new Blob(['<h1>Verified</h1>'], { type: 'text/html' })) };
}
async function start(api: AssessmentApi) {
  render(<AssessmentApp api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Load Acme example' }));
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Start mocked assessment' }));
}
async function setupLive(api: AssessmentApi) {
  vi.mocked(api.capabilities).mockResolvedValue({ ...capabilities, mode: 'live', configured: true, demo_input: null });
  render(<AssessmentApp api={api} />);
  await screen.findByRole('heading', { name: 'Bifrost and Tavily' });
  fireEvent.change(screen.getByLabelText('Full answer'), { target: { value: input.answer_text } });
  fireEvent.change(screen.getByLabelText('Verification date'), { target: { value: input.verification_date } });
  fireEvent.change(screen.getByLabelText(/Primary domains/), { target: { value: input.primary_domains.join('\n') } });
}
function submitLive() {
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Start live assessment' }));
}
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('assessment boundary', () => {
  it('strips the fragment synchronously before authenticated requests; never stores tokens', async () => {
    window.history.replaceState(null, '', '/assessment.html#token=secret_launch_token_123456');
    const fetcher = vi.fn().mockImplementation(async () => {
      expect(window.location.hash).toBe('');
      return new Response(JSON.stringify(capabilities), { headers: { 'Content-Type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetcher);
    const storage = vi.spyOn(Storage.prototype, 'setItem');
    const token = consumeLaunchToken(window.location, window.history);
    expect(token).toBe('secret_launch_token_123456');
    await createAssessmentApi(token!).capabilities();
    expect(fetcher).toHaveBeenCalledWith('/api/capabilities', expect.objectContaining({ headers: { 'X-Assessment-Token': token }, credentials: 'omit', redirect: 'error', cache: 'no-store' }));
    expect(storage).not.toHaveBeenCalled();
    expect(consumeLaunchToken(window.location, window.history)).toBeNull();
  });
  it('rejects malformed and duplicate tokens and strips them anyway', () => {
    window.history.replaceState(null, '', '/#token=secret_launch_token_123456&token=another_secret_token_1234');
    expect(consumeLaunchToken(window.location, window.history)).toBeNull();
    expect(window.location.hash).toBe('');
  });
  it('authenticates mutations and all artifacts without token URLs or automatic retries', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(job), { headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetcher);
    const api = createAssessmentApi('memory_token');
    await api.approve(job.id, job.approval!, false);
    expect(fetcher).toHaveBeenLastCalledWith('/api/jobs/job-1/approval', expect.objectContaining({ method: 'POST', headers: { 'X-Assessment-Token': 'memory_token', 'Content-Type': 'application/json' }, body: JSON.stringify({ request: job.approval, approved: false }) }));
    fetcher.mockResolvedValueOnce(new Response('<h1>Report</h1>', { headers: { 'Content-Type': 'text/html' } }));
    await api.artifact(job.id, 'report.html');
    expect(fetcher).toHaveBeenLastCalledWith('/api/jobs/job-1/artifacts/report.html', expect.objectContaining({ headers: { 'X-Assessment-Token': 'memory_token' } }));
    fetcher.mockRejectedValueOnce(new Error('network failure with sensitive internals'));
    await expect(api.create('same-key', input)).rejects.toThrow('outcome may be unknown');
    expect(fetcher).toHaveBeenCalledTimes(3);
  });
  it('preserves HTTP rejection status without exposing response internals', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: 'invalid assessment input' }),
      { status: 400, headers: { 'Content-Type': 'application/json' } })));
    await expect(createAssessmentApi('memory_token').create('same-key', input)).rejects.toMatchObject({
      message: 'invalid assessment input', status: 400,
    });
  });
  it('counts Unicode code points without trimming or truncating the answer', () => {
    const answer = '😀'.repeat(8000);
    expect(validateInput({ ...input, answer_text: answer }).answer_text).toBe(answer);
    expect(() => validateInput({ ...input, answer_text: answer + 'a' })).toThrow(AssessmentInputError);
    expect(() => validateInput({ ...input, answer_text: ' \n\t' })).toThrow('Correct the highlighted');
    expect(validateInput({ ...input, answer_text: '  answer  ' }).answer_text).toBe('  answer  ');
  });
  it.each(['0000-01-01', '2026-02-29', '2026-02-30', '10000-01-01'])('rejects invalid verification and answer dates %s', value => {
    expect(() => validateInput({ ...input, verification_date: value })).toThrow(AssessmentInputError);
    expect(() => validateInput({ ...input, answer_date: value })).toThrow(AssessmentInputError);
  });
  it('accepts real leap dates and equal dates but rejects reversed dates', () => {
    expect(validateInput({ ...input, verification_date: '2024-02-29', answer_date: '2024-02-29' })).toBeTruthy();
    expect(validateInput({ ...input, verification_date: '0001-01-01' })).toBeTruthy();
    expect(() => validateInput({ ...input, answer_date: '2026-11-03' })).toThrow(AssessmentInputError);
  });
  it.each(['https://example.org', 'example.org/path', 'example.org:443', '*.example.org', 'example.org.', 'localhost', '127.0.0.1', 'host.123', 'example..org', '-host.org', 'host-.org', `${'a'.repeat(64)}.org`, 'éxample.org', 'example.org?x=1', 'host\\name.org', ...['localhost', 'local', 'internal', 'lan', 'invalid', 'test', 'example', 'home', 'corp'].map(suffix => `host.${suffix}`)])('rejects domain outside backend public policy: %s', domain => {
    expect(() => validateInput({ ...input, primary_domains: [domain] })).toThrow(AssessmentInputError);
  });
  it('checks domain count, duplicates, label, URL and aggregate plan bounds without rewriting', () => {
    const domains = Array.from({ length: 10 }, (_, i) => `host${i}.org`);
    expect(validateInput({ ...input, primary_domains: domains }).primary_domains).toEqual(domains);
    expect(() => validateInput({ ...input, primary_domains: [...domains, 'extra.org'] })).toThrow(AssessmentInputError);
    expect(() => validateInput({ ...input, primary_domains: ['example.org', 'example.org'] })).toThrow(AssessmentInputError);
    expect(validateInput({ ...input, primary_domains: [`${'a'.repeat(63)}.org`] })).toBeTruthy();
    const long = Array(32).fill('a'.repeat(63)).join('.');
    expect(() => validateInput({ ...input, primary_domains: [long] })).toThrow(AssessmentInputError);
    const aggregate = Array.from({ length: 10 }, (_, i) => `${i}.${Array(30).fill('a'.repeat(63)).join('.')}.org`);
    expect(() => validateInput({ ...input, primary_domains: aggregate })).toThrow(AssessmentInputError);
  });
  it.each([{ max_claims: 0 }, { max_claims: 9 }, { max_claims: 1.5 }, { max_attempts: 26 }, { max_attempts: 0 }, { max_seconds: 0 }, { max_seconds: 181 }, { max_seconds: NaN }])('rejects invalid budget %j', patch => {
    expect(() => validateInput({ ...input, ...patch })).toThrow(AssessmentInputError);
  });
  it('keeps malformed capabilities separate from local field validation', async () => {
    const malformed = { ...capabilities, demo_input: { ...input, answer_text: 'a'.repeat(8001) } };
    expect(() => validateCapabilities(malformed)).toThrow('Invalid assessment API response');
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher);
    expect(() => createAssessmentApi('memory_token').create('key', { ...input, primary_domains: ['https://example.org'] })).toThrow(AssessmentInputError);
    expect(fetcher).not.toHaveBeenCalled();
  });
  it('validates runtime responses and refuses malformed dates, states, approvals and artifacts', () => {
    expect(validateCapabilities(capabilities)).toEqual(capabilities);
    expect(validateJob(job)).toEqual(job);
    expect(() => validateCapabilities({ ...capabilities, configured: 'true' })).toThrow();
    expect(() => validateInput({ ...input, verification_date: '2026-02-30' })).toThrow();
    expect(() => validateInput({ ...input, primary_domains: [] })).toThrow();
    expect(() => validateJob({ ...job, state: 'truth' })).toThrow();
    expect(() => validateJob({ ...job, approval: null })).toThrow();
    expect(() => validateJob({ ...job, approval: { ...job.approval, sha256: 'bad' } })).toThrow();
    expect(() => validateJob({ ...job, artifacts: ['../../secret'] })).toThrow();
    expect(() => validateJob({ ...job, unexpected: true })).toThrow();
  });
});

describe('assessment progress', () => {
  it('shows an interrupted stage without marking the report ready', () => {
    const { rerender } = render(<AssessmentRoute job={job} demo={false} />);
    const stages = screen.getAllByRole('listitem');
    expect(stages[1]).toHaveAttribute('aria-current', 'step');
    rerender(<AssessmentRoute job={{ ...job, state: 'researching' }} demo={false} />);
    expect(stages[2]).toHaveAttribute('aria-current', 'step');
    rerender(<AssessmentRoute job={{ ...job, state: 'cancelled' }} demo={false} />);
    expect(stages[2]).toHaveTextContent('stopped here');
    expect(stages[3]).toHaveTextContent('not started');
  });
  it('distinguishes skipped research from a report that is ready', () => {
    render(<AssessmentRoute job={{ ...job, state: 'completed', approval: null, artifacts: ['report.html'] }} demo={false} />);
    const stages = screen.getAllByRole('listitem');
    expect(stages[1]).toHaveTextContent('skipped');
    expect(stages[2]).toHaveTextContent('skipped');
    expect(stages[3]).toHaveTextContent('complete');
  });
});

describe('local assessment experience', () => {
  it('defaults to enough attempts for the maximum four-claim journey', async () => {
    const api = mockApi();
    await setupLive(api);
    expect(screen.getByLabelText('Maximum claims')).toHaveValue(4);
    expect(screen.getByLabelText('Maximum attempts')).toHaveValue(9);
  });
  it('requires a launch token and never loads a demo bundle', () => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher);
    render(<AssessmentApp api={null} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Launch token missing');
    expect(fetcher).not.toHaveBeenCalled();
  });
  it('requires fresh explicit consent, labels mocks and uses only service demo input', async () => {
    const api = mockApi(); render(<AssessmentApp api={api} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Load Acme example' }));
    expect(screen.getByLabelText('Full answer')).toHaveValue('Mock answer');
    expect(screen.getByRole('button', { name: 'Start mocked assessment' })).toBeDisabled();
    expect(screen.getByText(/Configuration presence is not connectivity/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.change(screen.getByLabelText('Maximum claims'), { target: { value: '3' } });
    expect(screen.getByRole('checkbox')).not.toBeChecked();
    expect(api.create).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Start mocked assessment' }));
    await screen.findByRole('heading', { name: '2. Review exact search request' });
    expect(api.create).toHaveBeenCalledWith(expect.any(String), { ...input, max_claims: 3 });
  });
  it('keeps demo fields read-only, requires explicit loading, and resets consent on reload', async () => {
    const api = mockApi(); render(<AssessmentApp api={api} />);
    const load = await screen.findByRole('button', { name: 'Load Acme example' });
    expect(screen.getByLabelText('Full answer')).toHaveValue('');
    expect(screen.getByRole('checkbox')).toBeDisabled();
    for (const label of ['Full answer', 'Verification date', 'Answer date (optional)', 'Primary domains (one per line, at least one required)']) {
      expect(screen.getByLabelText(label)).toHaveAttribute('readonly');
    }
    fireEvent.click(load);
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.change(screen.getByLabelText('Full answer'), { target: { value: 'Not the demo' } });
    expect(screen.getByLabelText('Full answer')).toHaveValue(input.answer_text);
    expect(screen.getByLabelText('Maximum claims')).not.toHaveAttribute('readonly');
    fireEvent.click(load);
    expect(screen.getByRole('checkbox')).not.toBeChecked();
    expect(api.create).not.toHaveBeenCalled();
  });
  it('shows accessible local errors, focuses the first invalid field, and never reserves or sends invalid input', async () => {
    const api = mockApi(); await setupLive(api);
    const answer = screen.getByLabelText('Full answer');
    fireEvent.change(answer, { target: { value: '😀'.repeat(8001) } });
    fireEvent.change(screen.getByLabelText(/Primary domains/), { target: { value: 'https://example.org' } });
    submitLive();
    expect(screen.getByRole('alert')).toHaveTextContent('No request was sent');
    expect(screen.getByRole('alert')).not.toHaveTextContent('API response');
    expect(answer).toHaveFocus();
    expect(answer).toHaveAttribute('aria-invalid', 'true');
    expect(answer).toHaveAccessibleDescription(/8,001 \/ 8,000 Unicode code points.*Shorten the full answer/);
    expect(screen.getByLabelText(/Primary domains/)).toHaveAccessibleDescription(/Domain 1: use a hostname/);
    expect(answer).not.toBeDisabled();
    expect(screen.queryByRole('heading', { name: 'Creation outcome uncertain' })).not.toBeInTheDocument();
    expect(api.create).not.toHaveBeenCalled();
    fireEvent.change(answer, { target: { value: '😀'.repeat(8000) } });
    expect(screen.getByRole('checkbox')).not.toBeChecked();
    fireEvent.change(screen.getByLabelText(/Primary domains/), { target: { value: '  EXAMPLE.ORG  \n\nexample.com ' } });
    submitLive();
    await waitFor(() => expect(api.create).toHaveBeenCalledOnce());
    expect(vi.mocked(api.create).mock.calls[0][1]).toEqual({ ...input, answer_text: '😀'.repeat(8000), primary_domains: ['example.org', 'example.com'] });
  });
  it.each([
    ['Full answer', '   ', 'Enter a nonblank full answer'],
    ['Verification date', '', 'Enter a valid verification date'],
    ['Answer date (optional)', '2026-11-03', 'Answer date must be on or before'],
    ['Primary domains (one per line, at least one required)', ' EXAMPLE.ORG \nexample.org', 'Remove duplicate domains'],
    ['Primary domains (one per line, at least one required)', Array.from({ length: 11 }, (_, i) => `host${i}.org`).join('\n'), 'Use at most 10'],
    ['Maximum claims', '1.5', 'Enter a whole number from 1 to 8'],
    ['Maximum attempts', '26', 'Enter a whole number from 1 to 25'],
    ['Deadline (seconds, includes review)', '0', 'Enter a deadline greater than 0'],
  ])('focuses invalid %s with actionable guidance', async (label, value, message) => {
    const api = mockApi(); await setupLive(api);
    const field = screen.getByLabelText(label);
    fireEvent.change(field, { target: { value } });
    submitLive();
    expect(field).toHaveFocus();
    expect(field).toHaveAttribute('aria-invalid', 'true');
    expect(field).toHaveAccessibleDescription(expect.stringContaining(message));
    expect(api.create).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: 'Retry identical creation request' })).not.toBeInTheDocument();
  });
  it('disables unconfigured live creation without implying connection health', async () => {
    const api = mockApi(); vi.mocked(api.capabilities).mockResolvedValue({ ...capabilities, mode: 'live', demo_input: null });
    render(<AssessmentApp api={api} />);
    expect(await screen.findByText(/Live creation disabled/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start live assessment' })).toBeDisabled();
    expect(screen.getByLabelText('Full answer')).toBeDisabled();
  });
  it.each([new Error('Outcome unknown'), new AssessmentRequestError('Service failed', 503), new Error('Invalid assessment API response. No action was retried.')])('retains immutable input and idempotency on uncertain creation (%s), with no automatic retry', async failure => {
    const api = mockApi(); vi.mocked(api.create).mockRejectedValueOnce(failure);
    await start(api);
    expect(await screen.findByRole('heading', { name: 'Creation outcome uncertain' })).toBeInTheDocument();
    expect(api.create).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText('Full answer')).toBeDisabled();
    const original = vi.mocked(api.create).mock.calls[0];
    fireEvent.click(screen.getByRole('button', { name: 'Retry identical creation request' }));
    await screen.findByRole('heading', { name: '2. Review exact search request' });
    expect(vi.mocked(api.create).mock.calls[1]).toEqual(original);
    expect(api.create).toHaveBeenCalledTimes(2);
  });
  it.each([400, 401, 403, 404, 409, 413, 422, 429, 499])('allows correcting a definitively rejected creation (%s) with fresh consent', async status => {
    const api = mockApi();
    vi.mocked(api.create).mockRejectedValueOnce(new AssessmentRequestError('Invalid assessment input', status));
    await setupLive(api);
    submitLive();
    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid assessment input');
    expect(screen.queryByRole('heading', { name: 'Creation outcome uncertain' })).not.toBeInTheDocument();
    expect(screen.getByLabelText('Full answer')).not.toBeDisabled();
    expect(screen.getByRole('checkbox')).not.toBeChecked();
    const originalKey = vi.mocked(api.create).mock.calls[0][0];
    fireEvent.change(screen.getByLabelText('Full answer'), { target: { value: 'Corrected answer' } });
    submitLive();
    await screen.findByRole('heading', { name: '2. Review exact search request' });
    expect(vi.mocked(api.create).mock.calls[1][0]).not.toBe(originalKey);
    expect(vi.mocked(api.create).mock.calls[1][1].answer_text).toBe('Corrected answer');
  });
  it.each([true, false])('renders literal escaped queries and transmits exact approval decision %s', async approved => {
    const api = mockApi(); const { container } = render(<AssessmentApp api={api} />);
    // The service may answer before its worker leaves awaiting_approval.
    vi.mocked(api.approve).mockResolvedValue(job);
    fireEvent.click(await screen.findByRole('button', { name: 'Load Acme example' }));
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Start mocked assessment' }));
    await screen.findByRole('heading', { name: '2. Review exact search request' });
    expect(container.querySelector('pre')?.textContent).toBe(JSON.stringify(job.approval!.queries[0]));
    expect(container.querySelector('script')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: approved ? 'Approve exact mocked searches' : 'Decline searches' }));
    await waitFor(() => expect(api.approve).toHaveBeenCalledWith(job.id, job.approval, approved));
    // A snapshot still awaiting approval must not offer a second, conflicting decision.
    expect(await screen.findByText('Decision recorded. Waiting for the service to continue.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Decline searches' })).not.toBeInTheDocument();
  });
  it('offers a fresh decision for a later job with an identical approval request', async () => {
    const api = mockApi(); render(<AssessmentApp api={api} />);
    vi.mocked(api.approve).mockResolvedValue({ ...job, state: 'declined', approval: null });
    fireEvent.click(await screen.findByRole('button', { name: 'Load Acme example' }));
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Start mocked assessment' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Decline searches' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Prepare another assessment' }));
    vi.mocked(api.create).mockResolvedValue({ ...job, id: 'b'.repeat(32) });
    fireEvent.click(screen.getByRole('button', { name: 'Load Acme example' }));
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Start mocked assessment' }));
    expect(await screen.findByRole('button', { name: 'Approve exact mocked searches' })).not.toBeDisabled();
  });
  it('cancels cooperatively without freeing the active slot in the UI', async () => {
    const api = mockApi(); await start(api);
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel assessment' }));
    expect(await screen.findByRole('button', { name: 'Cancellation requested' })).toBeDisabled();
    expect(api.cancel).toHaveBeenCalledWith(job.id);
    expect(screen.queryByRole('button', { name: 'Prepare another assessment' })).not.toBeInTheDocument();
  });
  it('polls active jobs and pauses on failures until explicit refresh', async () => {
    const api = mockApi(); await start(api);
    await screen.findByRole('heading', { name: '2. Review exact search request' });
    // Explicit refresh starts a new polling effect under fake timers.
    vi.useFakeTimers();
    fireEvent.click(screen.getByRole('button', { name: 'Refresh status' }));
    await act(async () => { await Promise.resolve(); });
    vi.mocked(api.job).mockRejectedValueOnce(new Error('Status unavailable'));
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(screen.getByRole('alert')).toHaveTextContent('Status unavailable');
    const calls = vi.mocked(api.job).mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(api.job).toHaveBeenCalledTimes(calls);
    expect(screen.getByRole('button', { name: 'Approve exact mocked searches' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Refresh status' }));
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByRole('button', { name: 'Approve exact mocked searches' })).not.toBeDisabled();
  });
  it('fetches the verified report into an opaque sandbox and downloads blobs, not API navigations', async () => {
    const api = mockApi(); vi.mocked(api.create).mockResolvedValue({ ...job, state: 'partial', approval: null, artifacts: ['report.html', 'report.json'] });
    const reportBlob = new Blob(['<h1>Verified</h1>']);
    Object.defineProperty(reportBlob, 'text', { value: async () => '<h1>Verified</h1>' });
    vi.mocked(api.artifact).mockResolvedValue(reportBlob);
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn().mockReturnValue('blob:verified'), revokeObjectURL: vi.fn() }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { expect(this.href).toBe('blob:verified'); expect(this.download).toBe('report.json'); });
    await start(api);
    fireEvent.click(await screen.findByRole('button', { name: 'Open verified report' }));
    const iframe = await screen.findByTitle('Verified assessment report');
    // Popups only, so source links open in a new tab; never scripts or same-origin.
    expect(iframe).toHaveAttribute('sandbox', 'allow-popups allow-popups-to-escape-sandbox');
    expect(iframe).not.toHaveAttribute('src');
    expect(iframe.getAttribute('srcdoc')).toContain('<h1>Verified</h1>');
    expect(iframe.getAttribute('srcdoc')).toContain("default-src 'none'");
    expect(api.artifact).toHaveBeenCalledWith(job.id, 'report.html');
    fireEvent.click(screen.getByRole('button', { name: 'Download report.json' }));
    await waitFor(() => expect(click).toHaveBeenCalledOnce());
    expect(api.artifact).toHaveBeenCalledWith(job.id, 'report.json');
  });
});
