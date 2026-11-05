// @ts-check
/**
 * Typed client for the ISOP backend. Same origin only; no credentials or secrets ever live in the browser.
 *
 * @typedef {"running"|"completed"|"failed"|"interrupted"} CaseStatus
 * @typedef {"ALLOW"|"REVIEW"|"REJECT"} Decision
 *
 * @typedef {Object} CaseEvent
 * @property {number} event_id
 * @property {string} case_id
 * @property {number} seq
 * @property {string} type
 * @property {string} stage
 * @property {string} status
 * @property {string} ts
 * @property {Record<string, any>} details
 *
 * @typedef {Object} CaseSummary
 * @property {string} case_id
 * @property {string} created_at
 * @property {string} updated_at
 * @property {CaseStatus} status
 * @property {string} email_masked
 * @property {Decision|null} decision
 * @property {string|null} planner_mode
 * @property {number|null} composite_score
 * @property {number|null} duration_ms
 * @property {number|null} tools_invoked
 * @property {string|null} error
 *
 * @typedef {Object} RegistrationPayload
 * @property {string} first_name
 * @property {string} last_name
 * @property {string} email
 * @property {string} [phone]
 * @property {string} ip_address
 * @property {string} device_id
 * @property {string} [device_fingerprint]
 * @property {string} [country]
 *
 * @typedef {Object} SubmitResult
 * @property {string} case_id
 * @property {CaseStatus} status
 * @property {boolean} created
 * @property {Record<string, string>} links
 *
 * @typedef {Object} Scenario
 * @property {string} id
 * @property {string} title
 * @property {Decision} expected
 * @property {string} summary
 * @property {RegistrationPayload} payload
 *
 * @typedef {Object} Health
 * @property {string} status
 * @property {string} version
 * @property {{llm_provider:string, model:string|null, llm_fallback:string, offline:boolean, tool_transport:string}} config
 * @property {Record<string, any>} llm
 * @property {string[]} mcp_tools
 * @property {string} policy_version
 */

export class ApiError extends Error {
  /** @param {number} status @param {string} message @param {any} [detail] */
  constructor(status, message, detail) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

/**
 * @param {string} path
 * @param {RequestInit} [init]
 * @returns {Promise<{status:number, body:any}>}
 */
async function call(path, init = {}) {
  let res;
  try {
    res = await fetch(path, { ...init, headers: { Accept: "application/json", ...(init.headers || {}) } });
  } catch (e) {
    throw new ApiError(0, "The ISOP service is not reachable. Is the server running?");
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = body && body.detail;
    const msg = typeof detail === "string" ? detail : res.status === 422 ? "Some fields are invalid." : `Request failed (${res.status})`;
    throw new ApiError(res.status, msg, detail);
  }
  return { status: res.status, body };
}

const enc = encodeURIComponent;

export const api = {
  /** @returns {Promise<Health>} */
  health: async () => (await call("/health")).body,
  /** @returns {Promise<Scenario[]>} */
  scenarios: async () => (await call("/demo/scenarios")).body,
  /**
   * Starts an investigation and returns at once; the backend keeps working whatever the UI does.
   * @param {RegistrationPayload} payload @param {string} idempotencyKey @returns {Promise<SubmitResult>}
   */
  submit: async (payload, idempotencyKey) => (await call("/register/check?mode=async", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(payload),
  })).body,
  /** @param {number} [limit] @returns {Promise<CaseSummary[]>} */
  list: async (limit = 50) => (await call(`/registrations?limit=${limit}`)).body,
  /** @param {string} id @returns {Promise<CaseSummary>} */
  get: async (id) => (await call(`/registrations/${enc(id)}`)).body,
  /** @param {string} id @param {number} [after] @returns {Promise<{case_id:string, status:CaseStatus, events:CaseEvent[]}>} */
  events: async (id, after = 0) => (await call(`/registrations/${enc(id)}/events?after=${after}`)).body,
  /** @param {string} id @returns {Promise<{status:number, body:any}>} 200 = result, 202 = running, 409 = failed */
  result: async (id) => {
    const res = await fetch(`/registrations/${enc(id)}/result`, { headers: { Accept: "application/json" } });
    return { status: res.status, body: await res.json().catch(() => null) };
  },
  /** @param {string} id @param {number} after */
  streamUrl: (id, after) => `/registrations/${enc(id)}/stream?after=${after}`,
};
