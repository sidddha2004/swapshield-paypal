/**
 * Hosted demo mode (NEXT_PUBLIC_SWAPSHIELD_DEMO=true).
 *
 * Replays responses recorded from the real SwapShield API (GPU verification,
 * resolution agent, evidence PDFs; mock PayPal) so the dashboard works without
 * the Python service. Confirming applies the same hard rules as the server
 * (allowed actions, refund cap, wording, no double execution) and never calls PayPal.
 */
import type { CaseAction, ExecuteBody, PayPalCase, ResolutionAction, ResolutionDraft } from "@/lib/paypal-api"

export const DEMO = process.env.NEXT_PUBLIC_SWAPSHIELD_DEMO === "true"

type Recorded = {
  verification: NonNullable<PayPalCase["verification"]>
  draft: ResolutionDraft
  photos: { dispatch: string; return: string }
  report: string
}
type DemoData = { cases: PayPalCase[]; recorded: Record<string, Recorded> }

// Deadlines relative to the visitor's clock, so the demo never shows every case as overdue.
const DUE_IN_HOURS: Record<string, number> = { "CASE-PP-D-27811": 41, "CASE-PP-D-27803": 7 * 24, "CASE-PP-D-27815": 10 * 24 }
const PROHIBITED = ["fraud", "fraudulent", "scam", "criminal", "guilty", "reject the refund", "block the customer"]
const ALLOWED: Record<string, ResolutionAction[]> = {
  approve: ["refund_full", "refund_partial"],
  review: ["submit_evidence", "refund_full", "refund_partial"],
  recapture: ["request_photos"],
}

let state: Map<string, PayPalCase> | null = null
let recorded: Record<string, Recorded> = {}

class DemoError extends Error {}

const now = () => Date.now() / 1000
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

async function load() {
  if (state) return state
  const response = await fetch("/demo/demo-data.json", { cache: "force-cache" })
  const data = (await response.json()) as DemoData
  recorded = data.recorded
  state = new Map(
    data.cases.map((item) => [
      item.case_id,
      {
        ...item,
        seller_response_due: new Date(Date.now() + (DUE_IN_HOURS[item.case_id] ?? 72) * 3_600_000).toISOString(),
        workflow_status: "awaiting_evidence",
        verification: null,
        draft: null,
        has_report: false,
        actions: [{ action: "webhook", actor: "paypal", request_id: null, detail: { event_type: "CUSTOMER.DISPUTE.CREATED" }, created_at: now() }],
      } satisfies PayPalCase,
    ]),
  )
  return state
}

function log(item: PayPalCase, action: string, actor: string, detail: Record<string, unknown> = {}, requestId: string | null = null) {
  const entry: CaseAction = { action, actor, request_id: requestId, detail, created_at: now() }
  item.actions = [...(item.actions ?? []), entry]
}

function snapshot(item: PayPalCase): PayPalCase {
  return structuredClone(item)
}

function findCase(id: string) {
  const item = state?.get(id)
  if (!item) throw new DemoError("Case not found")
  return item
}

export function demoPhotos(caseId: string) {
  return recorded[caseId]?.photos ?? null
}

export async function demoListCases() {
  const cases = await load()
  const visible = [...cases.values()]
  visible.sort((a, b) => Number(a.workflow_status === "resolved") - Number(b.workflow_status === "resolved") || (a.seller_response_due ?? "").localeCompare(b.seller_response_due ?? ""))
  return { cases: visible.map(snapshot), paypal: { configured: true, mode: "demo" } }
}

export async function demoSync() {
  const cases = await load()
  await pause(500)
  return { synced: cases.size, mode: "demo" }
}

export async function demoGetCase(id: string) {
  await load()
  return snapshot(findCase(id))
}

export async function demoVerify(id: string) {
  await load()
  const item = findCase(id)
  if (item.workflow_status === "resolved") throw new DemoError("This case has already been resolved on PayPal")
  await pause(1400)
  const result = recorded[id]
  item.verification = structuredClone(result.verification)
  item.has_report = true
  item.draft = null
  item.workflow_status = "verified"
  log(item, "verified", "swapshield", { decision: result.verification.risk.decision, score: result.verification.risk.score })
  return snapshot(item)
}

export async function demoDraft(id: string) {
  await load()
  const item = findCase(id)
  if (!item.verification) throw new DemoError("run verification before drafting a resolution")
  if (item.workflow_status === "resolved") throw new DemoError("This case has already been resolved on PayPal")
  await pause(700)
  item.draft = structuredClone(recorded[id].draft)
  item.workflow_status = "drafted"
  log(item, "drafted", `resolution_agent:${item.draft.source}`, { proposed_action: item.draft.proposed_action, refund_amount: item.draft.refund_amount })
  return snapshot(item)
}

export async function demoExecute(id: string, body: ExecuteBody) {
  await load()
  const item = findCase(id)
  if (!body.confirm) throw new DemoError("Execution requires explicit reviewer confirmation")
  if (!item.verification) throw new DemoError("run verification before resolving this dispute")
  const permitted = ALLOWED[item.verification.risk.decision] ?? []
  if (!permitted.includes(body.action)) throw new DemoError(`action '${body.action}' is not permitted for a '${item.verification.risk.decision}' recommendation`)
  const text = `${body.buyer_message} ${body.evidence_notes}`.toLowerCase()
  if (PROHIBITED.some((term) => text.includes(term))) throw new DemoError("message contains accusatory or prohibited language")
  if (body.action !== "submit_evidence" && !body.buyer_message.trim()) throw new DemoError("a buyer message is required for this action")
  if (body.action === "submit_evidence" && !body.evidence_notes.trim()) throw new DemoError("evidence notes are required to submit evidence")
  const cap = Number(item.captured_amount ?? item.amount)
  let amount = cap
  if (body.action === "refund_partial") {
    amount = Number(body.refund_amount)
    if (!Number.isFinite(amount) || amount <= 0) throw new DemoError("refund amount must be positive")
    if (amount >= cap) throw new DemoError(`partial refund must be below the captured amount ${cap.toFixed(2)}`)
  }

  let requestId = `swapshield-${id}-${body.action}`
  if (body.action === "request_photos") {
    requestId += `-${(item.actions ?? []).filter((entry) => entry.action === "executed:request_photos").length + 1}`
  } else {
    const replay = (item.actions ?? []).find((entry) => entry.request_id === requestId && entry.action.startsWith("executed:"))
    if (replay) return { ...snapshot(item), execution: { ...replay.detail, replayed: true } }
  }
  if (item.workflow_status === "resolved") throw new DemoError("This case has already been resolved on PayPal")

  await pause(900)
  const detail: Record<string, unknown> =
    body.action === "refund_full" || body.action === "refund_partial"
      ? { paypal_refund_id: `DEMO-REFUND-${Math.random().toString(36).slice(2, 10).toUpperCase()}`, paypal_status: "COMPLETED", amount: amount.toFixed(2), currency: item.currency }
      : body.action === "submit_evidence"
        ? { paypal_status: "UNDER_REVIEW", files: [`swapshield-${item.dispute_id}.pdf`] }
        : { paypal_status: "MESSAGE_SENT" }
  detail.paypal_mode = "demo (no PayPal call)"
  log(item, `executed:${body.action}`, `reviewer:${body.reviewer_name.trim()}`, detail, requestId)
  item.workflow_status = body.action === "request_photos" ? "awaiting_evidence" : "resolved"
  return { ...snapshot(item), execution: detail }
}

export function demoReportUrl(id: string) {
  return recorded[id]?.report ?? "#"
}
