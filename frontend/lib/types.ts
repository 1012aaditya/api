/** Mirrors the API response shapes. Kept hand-written and small on purpose. */

export interface ApiError {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

export interface Envelope<T> {
  success: boolean;
  request_id: string;
  data: T;
}

export interface Organization {
  id: string;
  name: string;
  slug: string;
  plan: string;
}

export interface UserProfile {
  id: string;
  email: string;
  full_name: string | null;
  role: string;
  created_at: string;
  organization: Organization;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in_seconds: number;
  user: UserProfile;
}

export interface ApiKeySummary {
  id: string;
  name: string;
  environment: string;
  masked_key: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
  expires_at: string | null;
  active: boolean;
}

export interface CreatedApiKey extends ApiKeySummary {
  /** Returned exactly once, at creation. Not recoverable afterwards. */
  key: string;
}

export interface DocumentSummary {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  page_count: number;
  document_type: string;
  status: string;
  request_id: string | null;
  stored: boolean;
  retention_expires_at: string | null;
  purged_at: string | null;
  created_at: string;
}

export type CheckStatus = "passed" | "warning" | "failed" | "not_checked";

export interface ValidationCheck {
  name: string;
  status: CheckStatus;
  message?: string | null;
  details?: Record<string, unknown> | null;
}

export interface ValidationSummary {
  overall: CheckStatus;
  checks: ValidationCheck[];
  gstin_format_valid: boolean | null;
  calculation_matches: boolean | null;
  required_fields_present: boolean | null;
}

export type ConfidenceBand = "high" | "medium" | "low";

export interface FieldConfidence {
  confidence: number;
  band: ConfidenceBand;
  page?: number;
  source_text?: string;
}

export interface ConfidenceSummary {
  overall: number | null;
  band: ConfidenceBand | null;
  fields: Record<string, FieldConfidence>;
  low_confidence_fields: string[];
}

export interface ProcessingSummary {
  duration_ms: number;
  /** Which tiers contributed, cheapest first: qr, text_layer, model. */
  tiers: string[];
  model_called: boolean;
  escalation_reason: string | null;
  notes: string[];
  pages: number;
  provider: string;
  model: string;
  prompt_version: string;
  provider_latency_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  estimated_cost_usd: number | null;
}

export interface ExtractionResponse {
  success: true;
  request_id: string;
  document_id: string;
  extraction_id: string;
  data: Record<string, unknown>;
  confidence: ConfidenceSummary;
  validation: ValidationSummary;
  processing: ProcessingSummary;
}

export interface StoredExtraction {
  id: string;
  document_id: string;
  request_id: string | null;
  status: string;
  document_type: string;
  data: Record<string, unknown> | null;
  confidence: Record<string, FieldConfidence> | null;
  overall_confidence: number | null;
  validation: { overall: CheckStatus; checks: ValidationCheck[] } | null;
  provider: string | null;
  model: string | null;
  prompt_version: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  estimated_cost_usd: number | null;
  provider_latency_ms: number | null;
  total_latency_ms: number | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
}

export interface DailyPoint {
  day: string;
  requests: number;
  successful: number;
  failed: number;
  documents: number;
}

export interface UsageSummary {
  window_days: number;
  totals: {
    requests: number;
    successful_requests: number;
    failed_requests: number;
    pages: number;
    average_duration_ms: number | null;
    estimated_cost_usd: number;
  };
  quota: {
    documents_used: number;
    monthly_quota: number;
    remaining: number;
    percent_used: number;
    period_start: string;
    rate_limit_per_minute: number;
  };
  daily: DailyPoint[];
  success_rate: number | null;
}

export interface UsageEvent {
  id: string;
  request_id: string | null;
  endpoint: string;
  event_type: string;
  status_code: number;
  success: boolean;
  billable: boolean;
  pages: number;
  model: string | null;
  duration_ms: number | null;
  estimated_cost_usd: number | null;
  error_code: string | null;
  document_id: string | null;
  created_at: string;
}

export type JobState = "queued" | "processing" | "completed" | "failed";

export interface JobAccepted {
  success: true;
  request_id: string;
  job_id: string;
  document_id: string;
  status: JobState;
}

export interface JobStatus {
  id: string;
  status: JobState;
  document_id: string;
  document_type: string;
  request_id: string | null;
  extraction_id: string | null;
  attempts: number;
  max_attempts: number;
  error: { code: string; message: string } | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export type WebhookEventName =
  | "document.processing"
  | "document.completed"
  | "document.failed";

export interface WebhookSummary {
  id: string;
  url: string;
  description: string | null;
  events: string[];
  is_active: boolean;
  consecutive_failures: number;
  last_delivery_at: string | null;
  disabled_at: string | null;
  created_at: string;
}

export interface CreatedWebhook extends WebhookSummary {
  /** Derived, not stored. Returned once, at creation or rotation. */
  secret: string;
}

export interface DeliverySummary {
  id: string;
  webhook_id: string;
  event: string;
  status: "pending" | "delivered" | "failed";
  job_id: string | null;
  document_id: string | null;
  attempts: number;
  max_attempts: number;
  response_status: number | null;
  error: string | null;
  next_attempt_at: string;
  delivered_at: string | null;
  created_at: string;
  payload: Record<string, unknown>;
}

export interface RejectedFile {
  filename: string;
  code: string;
  message: string;
}

export interface BatchAccepted {
  success: boolean;
  request_id: string;
  batch_id: string;
  accepted: number;
  rejected: RejectedFile[];
  job_ids: string[];
}

export interface BatchProgress {
  id: string;
  name: string | null;
  document_count: number;
  rejected_count: number;
  total: number;
  queued: number;
  processing: number;
  completed: number;
  failed: number;
  done: boolean;
  created_at: string;
}

// --- Tally -------------------------------------------------------------

export interface Ledger {
  id: string;
  name: string;
  gstin: string | null;
  parent_group: string | null;
}

export interface LedgerImportResult {
  imported: number;
  replaced: number;
  aliases_kept: number;
}

export interface TallySettings {
  company_name: string | null;
  voucher_type: string;
  purchase_ledger: string | null;
  cgst_ledger: string | null;
  sgst_ledger: string | null;
  igst_ledger: string | null;
  utgst_ledger: string | null;
  cess_ledger: string | null;
  round_off_ledger: string | null;
  other_charges_ledger: string | null;
  configured: boolean;
  ledger_count: number;
  unknown_ledgers: string[];
}

export interface LedgerSuggestion {
  ledger_id: string;
  ledger_name: string;
  score: number;
}

export interface UnmatchedSupplier {
  name: string;
  gstin: string | null;
  documents: number;
  suggestions: LedgerSuggestion[];
}

export interface VoucherPreview {
  document_id: string;
  filename: string | null;
  invoice_number: string | null;
  invoice_date: string | null;
  supplier_name: string | null;
  supplier_gstin: string | null;
  total: string | null;
  ledger_name: string | null;
  match_method: string;
  postable: boolean;
  blockers: string[];
  notes: string[];
}

export interface TallyPreview {
  postable: number;
  blocked: number;
  ledger_count: number;
  settings_configured: boolean;
  unmatched_suppliers: UnmatchedSupplier[];
  vouchers: VoucherPreview[];
}

/* --- CA operations ---------------------------------------------------
 *
 * These mirror `backend/app/schemas/operations.py`. The state strings are
 * left as the API sends them; the dashboard maps them to words and colours
 * in one place (`lib/practice.ts`) rather than inventing its own states.
 */

export interface PracticeClient {
  id: string;
  name: string;
  business_name: string | null;
  client_code: string | null;
  phone: string | null;
  whatsapp_phone: string | null;
  email: string | null;
  gstin: string | null;
  pan: string | null;
  status: string;
  preferred_channel: string;
  preferred_language: string;
  allow_automated_contact: boolean;
  automation_paused_reason: string | null;
  contact_state: string;
  last_contacted_at: string | null;
  last_response_at: string | null;
  created_at: string;
  open_cases: number;
  blocked_cases: number;
}

export interface Requirement {
  id: string;
  document_type: string;
  label: string;
  required: boolean;
  status: string;
  reason: string | null;
  received_document_id: string | null;
  requested_at: string | null;
  received_at: string | null;
}

export interface ComplianceCase {
  id: string;
  client_id: string;
  client_name: string | null;
  type: string;
  period: string;
  label: string;
  deadline: string | null;
  status: string;
  created_at: string;
  requirements: Requirement[];
  outstanding: string[];
  open_exceptions: number;
}

export interface ReviewException {
  id: string;
  type: string;
  severity: string;
  message: string;
  status: string;
  client_id: string | null;
  client_name: string | null;
  case_id: string | null;
  document_id: string | null;
  details: Record<string, unknown>;
  created_at: string;
  resolved_at: string | null;
}

export interface PracticeTask {
  id: string;
  title: string;
  description: string | null;
  priority: string;
  status: string;
  client_id: string | null;
  client_name: string | null;
  case_id: string | null;
  due_at: string | null;
  created_by: string;
  created_at: string;
}

export interface AgentEvent {
  id: string;
  actor_type: string;
  action: string;
  summary: string;
  client_id: string | null;
  client_name: string | null;
  case_id: string | null;
  details: Record<string, unknown>;
  created_at: string;
}

export interface ConversationMessage {
  id: string;
  direction: string;
  type: string;
  body: string | null;
  status: string;
  detected_intent: string | null;
  sent_by_agent: boolean;
  created_at: string;
}

export interface Conversation {
  id: string;
  client_id: string;
  client_name: string | null;
  channel: string;
  status: string;
  last_message_at: string | null;
  messages: ConversationMessage[];
}

export interface CommandCentre {
  clients_total: number;
  clients_blocked: number;
  cases_blocked: number;
  cases_ready: number;
  cases_completed: number;
  documents_awaiting_review: number;
  exceptions_open: number;
  tasks_open: number;
  tasks_overdue: number;
  calls_required: number;
  messages_sent_today: number;
  message_limit_per_day: number;
  agent_enabled: boolean;
}

export interface AgentPolicy {
  enabled: boolean;
  allow_whatsapp: boolean;
  allow_voice_calls: boolean;
  allow_auto_followup: boolean;
  allow_auto_escalation: boolean;
  max_messages_per_day: number;
  max_calls_per_day: number;
  max_followups_per_case: number;
  first_reminder_hours: number;
  second_reminder_hours: number;
  voice_call_after_hours: number;
  classification_threshold: string;
  local_ai_only: boolean;
  quiet_hours_start: number;
  quiet_hours_end: number;
}

export interface AgentRunResult {
  scheduled: number;
  sent: number;
  skipped: string[];
}
