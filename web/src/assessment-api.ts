export type AssessmentMode = 'demo' | 'live';
export type AssessmentInput = {
  answer_text: string; verification_date: string; answer_date: string | null;
  primary_domains: string[]; max_claims: number; max_attempts: number; max_seconds: number; consent: true;
};
export type ApprovalRequest = { queries: string[]; primary_domains: string[]; sha256: string };
export const artifactNames = ['report.html', 'report.json', 'input.json', 'manifest.json'] as const;
export type ArtifactName = typeof artifactNames[number];
export const configurationKeys = ['BIFROST_URL', 'BIFROST_MODEL', 'BIFROST_VIRTUAL_KEY', 'TAVILY_API_KEY', 'BIFROST_SEARCH_RELAY'] as const;
export type Capabilities = {
  mode: AssessmentMode; configured: boolean; configuration: Record<typeof configurationKeys[number], boolean>;
  max_sessions: number; demo_input: AssessmentInput | null;
};
const states = ['extracting', 'awaiting_approval', 'researching', 'cancel_requested', 'completed', 'partial', 'declined', 'expired', 'cancelled', 'failed'] as const;
export type Job = {
  id: string; mode: AssessmentMode; state: typeof states[number]; remaining_seconds: number;
  approval: ApprovalRequest | null; stop_reason: string | null; attempts: number; artifacts: ArtifactName[];
};
export const isActive = (job: Job) => ['extracting', 'awaiting_approval', 'researching', 'cancel_requested'].includes(job.state);

function invalid(): never { throw new Error('Invalid assessment API response. No action was retried.'); }
function object(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid();
  const record = value as Record<string, unknown>;
  if (Object.keys(record).length !== keys.length || keys.some(key => !Object.hasOwn(record, key))) return invalid();
  return record;
}
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(item => typeof item === 'string');
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const integer = (value: unknown, min: number, max = Number.MAX_SAFE_INTEGER): value is number => finite(value) && Number.isInteger(value) && value >= min && value <= max;
const mode = (value: unknown): value is AssessmentMode => value === 'demo' || value === 'live';
const date = (value: unknown): value is string => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && !value.startsWith('0000-') && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
export const MAX_ANSWER_CODEPOINTS = 8000;
export type InputErrors = Partial<Record<keyof AssessmentInput, string>>;
export class AssessmentInputError extends Error {
  constructor(readonly fields: InputErrors) { super('Correct the highlighted assessment fields. No request was sent.'); }
}

function domainError(domains: unknown): string | undefined {
  if (!strings(domains) || domains.length === 0) return 'Enter at least one public hostname, one per line.';
  if (domains.length > 10) return 'Use at most 10 primary domains; remove extra lines.';
  if (new Set(domains).size !== domains.length) return 'Remove duplicate domains (including differences only in case or surrounding whitespace).';
  for (const [index, domain] of domains.entries()) {
    const prefix = `Domain ${index + 1}: `;
    // Mirror acquisition.validate_plan/public_url and contracts.safe_url; do not
    // parse as a URL (which could silently rewrite a hostname or accept a path).
    if (!/^[a-z0-9-]+(?:\.[a-z0-9-]+)+$/.test(domain)) return prefix + 'use a hostname such as example.org, not a URL, path, port, IP address or wildcard.';
    if (domain.length + 'https:///'.length > 2048) return prefix + 'hostname is too long (maximum 2039 ASCII characters).';
    if (domain.split('.').some(label => label.length > 63 || label.startsWith('-') || label.endsWith('-'))) return prefix + 'each label must have 1–63 characters and cannot start or end with a hyphen.';
    if (/\.(localhost|local|internal|lan|invalid|test|example|home|corp)$/.test(domain)) return prefix + 'use a public hostname, not a private or reserved suffix.';
    if (/^\d+$/.test(domain.split('.').at(-1)!)) return prefix + 'IP addresses and numeric final labels are not allowed.';
  }
  // The backend validates this exact placeholder acquisition plan, including its
  // UTF-8 JSON byte bound. All accepted domains are ASCII, so length is bytes.
  const plan = { schema_version: '1.0', format: 'reviewed_public_acquisition', public_reviewed: true, queries: ['input validation only'], urls: [], primary_domains: domains };
  if ((JSON.stringify(plan, null, 2) + '\n').length > 16384) return 'Primary domains exceed the 16,384-byte plan limit; shorten or remove domains.';
}

export function validateInput(value: unknown): AssessmentInput {
  let v: Record<string, unknown>;
  try { v = object(value, ['answer_text', 'verification_date', 'answer_date', 'primary_domains', 'max_claims', 'max_attempts', 'max_seconds', 'consent']); }
  catch { throw new AssessmentInputError({ answer_text: 'Assessment input has missing or unexpected fields.' }); }
  const errors: InputErrors = {};
  if (typeof v.answer_text !== 'string' || !v.answer_text.trim()) errors.answer_text = 'Enter a nonblank full answer.';
  else if (Array.from(v.answer_text).length > MAX_ANSWER_CODEPOINTS) errors.answer_text = 'Shorten the full answer to 8,000 Unicode code points or fewer; nothing is truncated automatically.';
  if (!date(v.verification_date)) errors.verification_date = 'Enter a valid verification date (YYYY-MM-DD), with a year from 0001 to 9999.';
  if (v.answer_date !== null && !date(v.answer_date)) errors.answer_date = 'Enter a valid answer date (YYYY-MM-DD), with a year from 0001 to 9999, or leave it blank.';
  else if (date(v.answer_date) && date(v.verification_date) && v.answer_date > v.verification_date) errors.answer_date = 'Answer date must be on or before the verification date.';
  const domains = domainError(v.primary_domains);
  if (domains) errors.primary_domains = domains;
  if (!integer(v.max_claims, 1, 8)) errors.max_claims = 'Enter a whole number from 1 to 8 for maximum claims.';
  if (!integer(v.max_attempts, 1, 25)) errors.max_attempts = 'Enter a whole number from 1 to 25 for maximum attempts.';
  if (!finite(v.max_seconds) || v.max_seconds <= 0 || v.max_seconds > 180) errors.max_seconds = 'Enter a deadline greater than 0 and at most 180 seconds.';
  if (v.consent !== true) errors.consent = 'Give explicit consent before starting an assessment.';
  if (Object.keys(errors).length) throw new AssessmentInputError(errors);
  return v as AssessmentInput;
}
export function validateApproval(value: unknown): ApprovalRequest {
  const v = object(value, ['queries', 'primary_domains', 'sha256']);
  if (!strings(v.queries) || !strings(v.primary_domains) || typeof v.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(v.sha256)) return invalid();
  return v as ApprovalRequest;
}
export function validateCapabilities(value: unknown): Capabilities {
  const v = object(value, ['mode', 'configured', 'configuration', 'max_sessions', 'demo_input']);
  const config = object(v.configuration, configurationKeys);
  if (!mode(v.mode) || typeof v.configured !== 'boolean' || !configurationKeys.every(key => typeof config[key] === 'boolean') || !integer(v.max_sessions, 1)) return invalid();
  if (v.demo_input !== null) {
    try { validateInput(v.demo_input); } catch { return invalid(); }
  }
  if (v.mode === 'demo' && v.demo_input === null) return invalid();
  return v as Capabilities;
}
export function validateJob(value: unknown): Job {
  const v = object(value, ['id', 'mode', 'state', 'remaining_seconds', 'approval', 'stop_reason', 'attempts', 'artifacts']);
  if (typeof v.id !== 'string' || !/^[a-zA-Z0-9_-]{1,128}$/.test(v.id) || !mode(v.mode) || !states.includes(v.state as Job['state']) ||
      !finite(v.remaining_seconds) || v.remaining_seconds < 0 || !integer(v.attempts, 0) ||
      (v.stop_reason !== null && typeof v.stop_reason !== 'string') || !strings(v.artifacts) ||
      v.artifacts.some(name => !artifactNames.includes(name as ArtifactName)) || new Set(v.artifacts).size !== v.artifacts.length) return invalid();
  if (v.approval !== null) validateApproval(v.approval);
  if (v.state === 'awaiting_approval' && v.approval === null) return invalid();
  return v as Job;
}

/** Consume once, before the application can issue any request. Never persist the token. */
export function consumeLaunchToken(location: Pick<Location, 'hash' | 'pathname' | 'search'>, history: Pick<History, 'replaceState'>): string | null {
  const fragment = location.hash;
  history.replaceState(null, '', location.pathname + location.search);
  const params = new URLSearchParams(fragment.slice(1));
  const tokens = params.getAll('token');
  return tokens.length === 1 && /^[A-Za-z0-9_-]{16,512}$/.test(tokens[0]) ? tokens[0] : null;
}

export class AssessmentRequestError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}

export function createAssessmentApi(token: string) {
  async function request(path: string, body?: unknown): Promise<Response> {
    let response: Response;
    try {
      response = await fetch(path, {
        method: body === undefined ? 'GET' : 'POST',
        headers: { 'X-Assessment-Token': token, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        credentials: 'omit', redirect: 'error', cache: 'no-store', referrerPolicy: 'no-referrer',
        signal: AbortSignal.timeout(15000),
      });
    } catch { throw new Error('Request interrupted. Its outcome may be unknown; no action was retried.'); }
    if (!response.ok) {
      let message = `Assessment request failed (${response.status}).`;
      try {
        const value: unknown = await response.json();
        const error = object(value, ['error']);
        if (typeof error.error === 'string') message = error.error;
      } catch { /* Do not echo untrusted HTML or transport internals. */ }
      throw new AssessmentRequestError(message, response.status);
    }
    return response;
  }
  async function json<T>(path: string, validate: (v: unknown) => T, body?: unknown): Promise<T> {
    const response = await request(path, body);
    if (!response.headers.get('content-type')?.toLowerCase().includes('application/json')) return invalid();
    try { return validate(await response.json()); } catch { return invalid(); }
  }
  const jobPath = (id: string) => `/api/jobs/${encodeURIComponent(id)}`;
  const jobResult = (id: string) => (value: unknown) => { const job = validateJob(value); if (job.id !== id) return invalid(); return job; };
  return {
    capabilities: () => json('/api/capabilities', validateCapabilities),
    create: (idempotency_key: string, input: AssessmentInput) => json('/api/jobs', validateJob, { idempotency_key, input: validateInput(input) }),
    job: (id: string) => json(jobPath(id), jobResult(id)),
    approve: (id: string, request: ApprovalRequest, approved: boolean) => json(`${jobPath(id)}/approval`, jobResult(id), { request: validateApproval(request), approved }),
    cancel: (id: string) => json(`${jobPath(id)}/cancel`, jobResult(id), {}),
    artifact: async (id: string, name: ArtifactName) => {
      if (!artifactNames.includes(name)) return invalid();
      const response = await request(`${jobPath(id)}/artifacts/${name}`);
      const expected = name === 'report.html' ? 'text/html' : 'application/json';
      if (!response.headers.get('content-type')?.toLowerCase().includes(expected)) return invalid();
      return response.blob();
    },
  };
}
export type AssessmentApi = ReturnType<typeof createAssessmentApi>;
