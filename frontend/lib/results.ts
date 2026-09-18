import type {
  ConfidenceBand,
  ConfidenceSummary,
  ExtractionResponse,
  ProcessingSummary,
  StoredExtraction,
  ValidationSummary,
} from "./types";

/**
 * One shape for a result, whichever way it was produced.
 *
 * The synchronous endpoint returns the whole thing in its response; the
 * asynchronous path returns a job, and the result is fetched afterwards. The
 * two payloads differ in shape but not in meaning, so they are normalised
 * here rather than branching through every rendering component.
 */
export interface PlaygroundResult {
  requestId: string | null;
  documentId: string;
  extractionId: string;
  data: Record<string, unknown>;
  confidence: ConfidenceSummary;
  validation: ValidationSummary | null;
  processing: Partial<Omit<ProcessingSummary, "duration_ms" | "pages">> & {
    duration_ms: number | null;
    pages: number | null;
  };
}

function bandFor(score: number | null): ConfidenceBand | null {
  if (score === null) return null;
  if (score >= 0.85) return "high";
  if (score >= 0.6) return "medium";
  return "low";
}

export function fromExtractionResponse(
  response: ExtractionResponse,
): PlaygroundResult {
  return {
    requestId: response.request_id,
    documentId: response.document_id,
    extractionId: response.extraction_id,
    data: response.data,
    confidence: response.confidence,
    validation: response.validation,
    processing: response.processing,
  };
}

export function fromStoredExtraction(
  stored: StoredExtraction,
  pages: number | null = null,
): PlaygroundResult {
  const fields = stored.confidence ?? {};
  return {
    requestId: stored.request_id,
    documentId: stored.document_id,
    extractionId: stored.id,
    data: stored.data ?? {},
    confidence: {
      overall: stored.overall_confidence,
      band: bandFor(stored.overall_confidence),
      fields,
      low_confidence_fields: Object.entries(fields)
        .filter(([, value]) => value.band === "low")
        .map(([path]) => path),
    },
    // The stored row keeps the checks but not the three headline booleans,
    // which are derived at response time. Null means "not recorded", not false.
    validation: stored.validation
      ? {
          overall: stored.validation.overall,
          checks: stored.validation.checks,
          gstin_format_valid: null,
          calculation_matches: null,
          required_fields_present: null,
        }
      : null,
    processing: {
      duration_ms: stored.total_latency_ms,
      provider_latency_ms: stored.provider_latency_ms,
      provider: stored.provider ?? undefined,
      model: stored.model ?? undefined,
      pages,
      prompt_version: stored.prompt_version ?? undefined,
      tiers: [],
      model_called: stored.provider !== null && stored.provider !== "none",
      input_tokens: stored.input_tokens,
      output_tokens: stored.output_tokens,
      estimated_cost_usd: stored.estimated_cost_usd,
    },
  };
}
