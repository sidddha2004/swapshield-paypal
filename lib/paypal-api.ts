import { DEMO, demoDraft, demoExecute, demoGetCase, demoListCases, demoReportUrl, demoSync, demoVerify } from "@/lib/demo"
import { responseError, type VerificationResponse } from "@/lib/live-api"

export type ResolutionAction = "refund_full" | "refund_partial" | "submit_evidence" | "request_photos"
export type WorkflowStatus = "awaiting_evidence" | "verified" | "drafted" | "resolved"

export type ResolutionDraft = {
  proposed_action: ResolutionAction
  refund_amount: string | null
  buyer_message: string
  evidence_notes: string
  rationale: string
  cited_evidence_ids: string[]
  allowed_actions: ResolutionAction[]
  source: "llm_api" | "deterministic_fallback"
  api_status: "used" | "not_configured" | "failed"
  model: string | null
  authority: "proposal_only"
}

export type CaseAction = {
  action: string
  actor: string
  request_id: string | null
  detail: Record<string, unknown>
  created_at: number
}

export type PayPalCase = {
  case_id: string
  dispute_id: string
  capture_id: string | null
  invoice_id: string | null
  reason: string | null
  dispute_status: string | null
  lifecycle_stage: string | null
  amount: string | null
  currency: string | null
  captured_amount: string | null
  seller_response_due: string | null
  item_sku: string | null
  item_name: string | null
  buyer_message: string | null
  workflow_status: WorkflowStatus
  has_report: boolean
  verification: (VerificationResponse & { records?: Record<string, string | number | null> }) | null
  draft: ResolutionDraft | null
  actions?: CaseAction[]
  execution?: Record<string, unknown>
}

export type PayPalStatus = { configured: boolean; mode: string }

export type ExecuteBody = {
  action: ResolutionAction
  reviewer_name: string
  confirm: true
  refund_amount?: string | null
  buyer_message: string
  evidence_notes: string
}

const base = (apiUrl: string) => apiUrl.replace(/\/$/, "")

async function call<T>(apiUrl: string, path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${base(apiUrl)}${path}`, { cache: "no-store", ...init })
  } catch {
    throw new Error("The SwapShield API is unreachable. Start FastAPI locally.")
  }
  const payload = (await response.json().catch(() => null)) as unknown
  if (!response.ok) throw new Error(responseError(payload, `Request failed with HTTP ${response.status}`))
  return payload as T
}

export const listCases = (apiUrl: string): Promise<{ cases: PayPalCase[]; paypal: PayPalStatus }> =>
  DEMO ? demoListCases() : call(apiUrl, "/v1/cases")

export const getCase = (apiUrl: string, caseId: string): Promise<PayPalCase> =>
  DEMO ? demoGetCase(caseId) : call(apiUrl, `/v1/cases/${encodeURIComponent(caseId)}`)

export const syncDisputes = (apiUrl: string): Promise<{ synced: number; mode: string }> =>
  DEMO ? demoSync() : call(apiUrl, "/v1/paypal/sync", { method: "POST" })

/** Demo mode only: replay the verification recorded from the real API for this case. */
export const verifyRecorded = (caseId: string): Promise<PayPalCase> => demoVerify(caseId)

export function verifyCase(
  apiUrl: string,
  caseId: string,
  values: {
    dispatchImage: File
    returnImage: File
    dispatchSku: string
    returnSku: string
    dispatchSerial?: string
    returnSerial?: string
    dispatchWeight?: string
    returnWeight?: string
  },
) {
  const form = new FormData()
  form.append("dispatch_image", values.dispatchImage)
  form.append("return_image", values.returnImage)
  form.append("dispatch_sku", values.dispatchSku.trim())
  form.append("return_sku", values.returnSku.trim())
  if (values.dispatchSerial?.trim()) form.append("dispatch_serial", values.dispatchSerial.trim())
  if (values.returnSerial?.trim()) form.append("return_serial", values.returnSerial.trim())
  if (values.dispatchWeight?.trim()) form.append("dispatch_weight_grams", values.dispatchWeight.trim())
  if (values.returnWeight?.trim()) form.append("return_weight_grams", values.returnWeight.trim())
  return call<PayPalCase>(apiUrl, `/v1/cases/${encodeURIComponent(caseId)}/verify`, { method: "POST", body: form })
}

export const draftResolution = (apiUrl: string, caseId: string): Promise<PayPalCase> =>
  DEMO ? demoDraft(caseId) : call(apiUrl, `/v1/cases/${encodeURIComponent(caseId)}/resolution/draft`, { method: "POST" })

export const executeResolution = (apiUrl: string, caseId: string, body: ExecuteBody): Promise<PayPalCase> =>
  DEMO
    ? demoExecute(caseId, body)
    : call(apiUrl, `/v1/cases/${encodeURIComponent(caseId)}/resolution/execute`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })

export const reportUrl = (apiUrl: string, caseId: string) =>
  DEMO ? demoReportUrl(caseId) : `${base(apiUrl)}/v1/cases/${encodeURIComponent(caseId)}/report.pdf`

export const ACTION_LABELS: Record<ResolutionAction, string> = {
  refund_full: "Full refund",
  refund_partial: "Partial refund",
  submit_evidence: "Submit evidence to PayPal",
  request_photos: "Ask buyer for clearer photos",
}

export function formatMoney(value: string | null, currency: string | null) {
  if (value == null) return "—"
  const amount = Number(value)
  if (!Number.isFinite(amount)) return value
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency: currency || "USD" }).format(amount)
  } catch {
    return `${value} ${currency ?? ""}`.trim()
  }
}
