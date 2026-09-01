/**
 * Centralized browser logging.
 *
 * The frontend had no logging at all: a render crash during a live exam left a
 * white screen and nothing to go on, and ~50 `catch` blocks discarded their
 * error entirely. Everything here exists to make an exam-day failure
 * reconstructable from a student's console, or from a screen-share, without
 * ever writing something that must not leave the browser.
 *
 * **Correlation.** The backend stamps every response with an `X-Request-ID`
 * header (see `backend/app/main.py`'s `request_context` middleware and
 * `backend/app/logging_config.py`). `api.ts` feeds the most recent one to
 * `setRequestId`, so a frontend line emitted just after a failed call carries
 * the same ID the server logged against it — which is the whole point: grep one
 * ID and get both halves of the story.
 *
 * **Redaction.** Mirrors the backend's `SENSITIVE_KEY_PARTS`: any context key
 * whose name contains password / token / secret / authorization / api_key /
 * cookie / pin / credential is replaced before it reaches the console. The
 * access and refresh JWTs live in localStorage, so an unredacted context object
 * is a real credential-leak path, not a theoretical one.
 *
 * **Never log**, redaction or not — these are the caller's responsibility
 * because no key-name rule can catch them:
 *   - answer content, selected options, or any student code / SQL
 *   - magic-link tokens, exam PINs, passwords
 *   - full email addresses (use `maskEmail`)
 *   - request or response bodies from `api.ts` — answers and code go through it
 *
 * Console only. There is deliberately no network sink and no dependency: a
 * logger that POSTs during an exam competes with auto-save for the same flaky
 * connection. If one is ever wanted, `emit()` below is the single place to add
 * it — every level funnels through there.
 */

export type LogLevel = 'debug' | 'info' | 'warn' | 'error';

const LEVEL_ORDER: Record<LogLevel, number> = { debug: 10, info: 20, warn: 30, error: 40 };

function resolveLevel(): LogLevel {
  const configured = String(import.meta.env.VITE_LOG_LEVEL ?? '').toLowerCase();
  if (configured in LEVEL_ORDER) return configured as LogLevel;
  // `info` in production too, matching the backend's LOG_LEVEL default: these
  // lines exist to make an exam-day failure reconstructable from a student's
  // console, and a level that hides them defeats the point. `debug` stays
  // available through VITE_LOG_LEVEL but nothing in the app emits at it.
  return 'info';
}

const threshold = LEVEL_ORDER[resolveLevel()];

// -------------------------------------------------------------- correlation

let currentRequestId: string | null = null;

/** Called by `api.ts` with the `X-Request-ID` off the most recent response. */
export function setRequestId(id: string | null): void {
  if (id) currentRequestId = id;
}

export function getRequestId(): string | null {
  return currentRequestId;
}

// ---------------------------------------------------------------- redaction

// Substring match, lowercased, against context keys. Kept in step with
// SENSITIVE_KEY_PARTS in backend/app/logging_config.py.
const SENSITIVE_KEY_PARTS = [
  'password',
  'passwd',
  'secret',
  'token',
  'authorization',
  'api_key',
  'apikey',
  'cookie',
  'session',
  'pin',
  'config_key',
  'credential',
  'private',
];

const REDACTED = '***redacted***';

function isSensitiveKey(key: string): boolean {
  const lowered = key.toLowerCase();
  return SENSITIVE_KEY_PARTS.some((part) => lowered.includes(part));
}

/**
 * Recursively scrub sensitive-looking values. Depth-bounded for the same reason
 * the backend's is: formatting a log line must never become the expensive or
 * recursive part of an exam page, and nothing worth logging nests that deep.
 */
function redact(value: unknown, depth = 0): unknown {
  if (depth > 4) return '...';
  if (value instanceof Error) {
    return { name: value.name, message: value.message, stack: value.stack };
  }
  if (Array.isArray(value)) return value.map((v) => redact(v, depth + 1));
  if (value && typeof value === 'object') {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      out[k] = isSensitiveKey(k) ? REDACTED : redact(v, depth + 1);
    }
    return out;
  }
  return value;
}

/**
 * `alice@example.com` -> `a***e@example.com`. Enough to correlate repeated
 * failures from one address across lines without writing the address itself.
 */
export function maskEmail(email: string | null | undefined): string {
  if (!email || !email.includes('@')) return 'unknown';
  const [local, domain] = [email.slice(0, email.indexOf('@')), email.slice(email.indexOf('@') + 1)];
  if (local.length <= 2) return `${local.slice(0, 1)}***@${domain}`;
  return `${local[0]}***${local[local.length - 1]}@${domain}`;
}

// ----------------------------------------------------------------- emission

export type LogContext = Record<string, unknown>;

const CONSOLE: Record<LogLevel, (...args: unknown[]) => void> = {
  debug: (...a) => console.debug(...a),
  info: (...a) => console.info(...a),
  warn: (...a) => console.warn(...a),
  error: (...a) => console.error(...a),
};

/**
 * The one funnel every log line passes through — add a remote sink here and
 * nowhere else.
 */
function emit(level: LogLevel, message: string, context?: LogContext): void {
  if (LEVEL_ORDER[level] < threshold) return;

  const payload: Record<string, unknown> = {
    ts: new Date().toISOString(),
    level,
    msg: message,
  };
  if (currentRequestId) payload.request_id = currentRequestId;
  if (context) Object.assign(payload, redact(context) as Record<string, unknown>);

  // Logging must never be the thing that breaks the page it was added to
  // observe — a getter on a context object throwing during serialization would
  // otherwise take down the caller's error path.
  try {
    CONSOLE[level](`[${level}] ${message}`, payload);
  } catch {
    /* nothing useful to do if the console itself rejects the payload */
  }
}

export const log = {
  debug: (message: string, context?: LogContext) => emit('debug', message, context),
  info: (message: string, context?: LogContext) => emit('info', message, context),
  warn: (message: string, context?: LogContext) => emit('warn', message, context),
  error: (message: string, context?: LogContext) => emit('error', message, context),
};

/**
 * Normalizes an unknown thrown value into loggable context. `catch (err)` gives
 * `unknown`, and half the call sites throw an ApiError while the rest can see a
 * TypeError from fetch or a string from a library.
 */
export function errorContext(err: unknown): LogContext {
  if (err instanceof Error) {
    return { error_name: err.name, error_message: err.message, stack: err.stack };
  }
  return { error_name: typeof err, error_message: String(err) };
}

// --------------------------------------------------------- global handlers

let globalHandlersInstalled = false;

/**
 * Catches what React's error boundary structurally cannot: throws from event
 * handlers, timers and async callbacks, plus every unhandled promise rejection.
 * Idempotent — StrictMode double-invokes the module's consumers in dev.
 */
export function installGlobalErrorHandlers(): void {
  if (globalHandlersInstalled) return;
  globalHandlersInstalled = true;

  window.addEventListener('error', (event: ErrorEvent) => {
    log.error('uncaught error', {
      ...errorContext(event.error ?? event.message),
      source: event.filename,
      line: event.lineno,
      column: event.colno,
    });
  });

  window.addEventListener('unhandledrejection', (event: PromiseRejectionEvent) => {
    log.error('unhandled promise rejection', errorContext(event.reason));
  });
}
