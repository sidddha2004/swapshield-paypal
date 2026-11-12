"use client"

import { FormEvent, useCallback, useEffect, useState } from "react"
import { AlertTriangle, CheckCircle2, FileDown, ImageOff, LoaderCircle, RefreshCw, ScanLine, Sparkles } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Field, UploadCard, usePreview } from "@/components/live-verification"
import { defaultApiUrl } from "@/lib/live-api"
import {
  ACTION_LABELS, draftResolution, executeResolution, formatMoney, getCase, listCases, reportUrl, syncDisputes,
  verifyCase, type CaseAction, type PayPalCase, type PayPalStatus, type ResolutionAction,
} from "@/lib/paypal-api"

type LoadState = "loading" | "ready" | "offline"

const STATUS: Record<PayPalCase["workflow_status"], { label: string; tone: string }> = {
  awaiting_evidence: { label: "Needs evidence", tone: "warning" },
  verified: { label: "Verified", tone: "neutral" },
  drafted: { label: "Ready to confirm", tone: "brand" },
  resolved: { label: "Resolved", tone: "positive" },
}

const REASONS: Record<string, string> = {
  MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED: "Not as described",
  MERCHANDISE_OR_SERVICE_NOT_RECEIVED: "Not received",
  UNAUTHORISED: "Unauthorised",
}

function percent(value: number) {
  return `${(value * 100).toFixed(1)}%`
}

function hoursLeft(iso: string | null) {
  if (!iso) return null
  return Math.round((new Date(iso).getTime() - Date.now()) / 36e5)
}

function dueLabel(iso: string | null) {
  const hours = hoursLeft(iso)
  if (hours === null) return "No deadline"
  if (hours < 0) return "Response overdue"
  if (hours < 48) return `${hours} h left`
  return `${Math.round(hours / 24)} days left`
}

function reason(value: string | null) {
  return value ? REASONS[value] ?? value.toLowerCase().replaceAll("_", " ") : "—"
}

function clock(seconds: number) {
  return new Date(seconds * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
}

function decisionMeta(decision?: string) {
  if (decision === "approve") return { label: "Matches dispatch", tone: "safe", icon: CheckCircle2 }
  if (decision === "recapture") return { label: "Needs clearer photos", tone: "uncertain", icon: ImageOff }
  return { label: "Needs a closer look", tone: "risk", icon: AlertTriangle }
}

function isOpen(item: PayPalCase) {
  return item.workflow_status !== "resolved"
}

export function PayPalDisputes({ onOpenCountChange }: { onOpenCountChange?: (count: number) => void }) {
  const [apiUrl] = useState(defaultApiUrl)
  const [cases, setCases] = useState<PayPalCase[]>([])
  const [paypal, setPaypal] = useState<PayPalStatus | null>(null)
  const [load, setLoad] = useState<LoadState>("loading")
  const [syncedAt, setSyncedAt] = useState<Date | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<PayPalCase | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const applyList = useCallback((next: { cases: PayPalCase[]; paypal: PayPalStatus }, focus?: string) => {
    setCases(next.cases)
    setPaypal(next.paypal)
    setLoad("ready")
    setSelectedId((current) => focus ?? current ?? next.cases.find(isOpen)?.case_id ?? next.cases[0]?.case_id ?? null)
    onOpenCountChange?.(next.cases.filter(isOpen).length)
  }, [onOpenCountChange])

  const refresh = useCallback(async (focus?: string) => {
    try {
      applyList(await listCases(apiUrl), focus)
    } catch {
      setLoad("offline")
    }
  }, [apiUrl, applyList])

  useEffect(() => {
    let active = true
    void listCases(apiUrl).then((next) => active && applyList(next), () => active && setLoad("offline"))
    return () => {
      active = false
    }
  }, [apiUrl, applyList])

  useEffect(() => {
    if (!selectedId) return
    let active = true
    void getCase(apiUrl, selectedId).then((next) => active && setDetail(next), () => undefined)
    return () => {
      active = false
    }
  }, [apiUrl, selectedId])

  async function run(label: string, task: () => Promise<PayPalCase | { synced: number }>) {
    setBusy(label)
    setError(null)
    try {
      const result = await task()
      if ("case_id" in result) {
        setDetail(result)
        await refresh(result.case_id)
      } else {
        setSyncedAt(new Date())
        await refresh()
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Request failed")
    } finally {
      setBusy(null)
    }
  }

  const open = cases.filter(isOpen)
  const next = [...open].sort((a, b) => (a.seller_response_due ?? "~").localeCompare(b.seller_response_due ?? "~"))[0]
  const finding = load === "loading" ? "Loading disputes…"
    : load === "offline" ? "Can't reach the SwapShield API."
      : cases.length === 0 ? "No PayPal disputes yet."
        : open.length === 0 ? `All ${cases.length} disputes are resolved.`
          : `${open.length} of ${cases.length} need a response. Next due: ${next.item_name ?? next.dispute_id}, ${dueLabel(next.seller_response_due)}.`
  const selected = detail && detail.case_id === selectedId ? detail : null

  return (
    <section>
      <div className="mb-6 flex flex-col justify-between gap-3 sm:flex-row sm:items-end">
        <div>
          <h1 className="page-title">Disputes</h1>
          <p className="mt-1 text-sm text-[var(--ink-muted)]">{finding}</p>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-[var(--ink-muted)]">
            {paypal ? (paypal.configured ? `PayPal ${paypal.mode}` : "PayPal not configured") : ""}
            {syncedAt ? ` · synced ${syncedAt.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}` : ""}
          </span>
          <Button variant="outline" size="sm" onClick={() => void run("sync", () => syncDisputes(apiUrl))} disabled={busy !== null || load !== "ready" || !paypal?.configured}>
            <RefreshCw className={busy === "sync" ? "animate-spin" : ""} /> Sync from PayPal
          </Button>
        </div>
      </div>

      {error && <p role="alert" className="notice negative mb-6">{error}</p>}

      <div className="grid gap-6 xl:grid-cols-[320px_minmax(0,1fr)]">
        <aside className="panel self-start p-2" aria-label="Dispute queue">
          {load === "loading" && [0, 1, 2].map((key) => (
            <div key={key} className="p-3"><span className="skeleton h-5 w-40" /><span className="skeleton mt-1.5 h-4 w-56" /></div>
          ))}
          {load === "offline" && (
            <div className="p-3 text-sm">
              <p>Couldn&apos;t load disputes. Start the API on port 8000.</p>
              <Button variant="link" size="sm" className="mt-1 h-auto p-0" onClick={() => { setLoad("loading"); void refresh() }}>Retry</Button>
            </div>
          )}
          {load === "ready" && cases.length === 0 && (
            <p className="p-3 text-sm text-[var(--ink-muted)]">Nothing to resolve. New disputes arrive through the PayPal webhook, or use Sync from PayPal.</p>
          )}
          {cases.map((item) => {
            const status = STATUS[item.workflow_status]
            const hours = hoursLeft(item.seller_response_due)
            return (
              <button key={item.case_id} type="button" className="queue-row" aria-current={item.case_id === selectedId} onClick={() => { setSelectedId(item.case_id); if (window.matchMedia("(max-width: 1279px)").matches) requestAnimationFrame(() => document.getElementById("case-detail")?.scrollIntoView({ behavior: "smooth", block: "start" })) }}>
                <div className="flex items-baseline justify-between gap-3">
                  <span className="truncate text-sm font-medium">{item.item_name ?? item.item_sku ?? "Disputed item"}</span>
                  <span className="num flex-none text-sm">{formatMoney(item.amount, item.currency)}</span>
                </div>
                <div className="mt-1.5 flex items-center justify-between gap-3">
                  <span className="truncate text-[13px] text-[var(--ink-muted)]">
                    <span className="font-mono text-xs">{item.dispute_id}</span> · {isOpen(item) ? <span className={hours !== null && hours < 0 ? "tone-negative" : undefined}>{dueLabel(item.seller_response_due)}</span> : reason(item.reason)}
                  </span>
                  <span className={`pill ${status.tone}`}>{status.label}</span>
                </div>
              </button>
            )
          })}
        </aside>

        <div id="case-detail" className="min-w-0 scroll-mt-4 space-y-6">
          {selected ? (
            <CaseDetail
              key={selected.case_id}
              apiUrl={apiUrl}
              item={selected}
              busy={busy}
              onVerify={(values) => run("verify", () => verifyCase(apiUrl, selected.case_id, values))}
              onDraft={() => run("draft", () => draftResolution(apiUrl, selected.case_id))}
              onExecute={(body) => run("execute", () => executeResolution(apiUrl, selected.case_id, body))}
            />
          ) : load === "ready" && cases.length > 0 ? (
            <div className="panel p-6 text-sm text-[var(--ink-muted)]">Loading the case…</div>
          ) : load === "loading" ? (
            <div className="panel p-6"><span className="skeleton h-6 w-64" /><span className="skeleton mt-3 h-4 w-96 max-w-full" /></div>
          ) : null}
        </div>
      </div>
    </section>
  )
}

type VerifyValues = Parameters<typeof verifyCase>[2]
type ExecuteBody = Parameters<typeof executeResolution>[2]

function CaseDetail({ apiUrl, item, busy, onVerify, onDraft, onExecute }: {
  apiUrl: string
  item: PayPalCase
  busy: string | null
  onVerify: (values: VerifyValues) => Promise<unknown>
  onDraft: () => Promise<unknown>
  onExecute: (body: ExecuteBody) => Promise<unknown>
}) {
  const resolved = item.workflow_status === "resolved"
  return (
    <>
      <CaseHeader item={item} />
      <EvidenceBlock apiUrl={apiUrl} item={item} busy={busy} disabled={resolved} onVerify={onVerify} />
      {item.verification && <ResolutionBlock key={JSON.stringify(item.draft)} item={item} busy={busy} disabled={resolved} onDraft={onDraft} onExecute={onExecute} />}
      <ActivityBlock actions={item.actions ?? []} />
    </>
  )
}

function lastAction(actions: CaseAction[], prefix: string) {
  return [...actions].reverse().find((entry) => entry.action.startsWith(prefix))
}

function CaseHeader({ item }: { item: PayPalCase }) {
  const actions = item.actions ?? []
  const executed = lastAction(actions, "executed:")
  const photosRequested = item.workflow_status === "awaiting_evidence" && executed?.action === "executed:request_photos"
  const created = (item as PayPalCase & { dispute?: { create_time?: string } }).dispute?.create_time
  const steps: Array<{ label: string; state: "done" | "current" | "todo"; meta: string }> = [
    { label: "Dispute received", state: "done", meta: created ? new Date(created).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : reason(item.reason) },
    {
      label: "Item verified",
      state: item.verification && !photosRequested ? "done" : "current",
      meta: photosRequested ? "Clearer photos requested" : item.verification ? decisionMeta(item.verification.risk.decision).label : "Upload photos",
    },
    {
      label: "Proposal drafted",
      state: item.draft ? "done" : item.verification && !photosRequested ? "current" : "todo",
      meta: item.draft ? ACTION_LABELS[item.draft.proposed_action] : "—",
    },
    {
      label: "Done on PayPal",
      state: item.workflow_status === "resolved" ? "done" : item.draft ? "current" : "todo",
      meta: item.workflow_status === "resolved" && executed ? ACTION_LABELS[executed.action.replace("executed:", "") as ResolutionAction] : item.draft ? "Waiting for your confirmation" : "—",
    },
  ]
  const hours = hoursLeft(item.seller_response_due)

  return (
    <div className="panel p-6">
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold leading-6">{item.item_name ?? item.item_sku ?? "Disputed item"}</h2>
          <p className="mt-1 text-[13px] leading-5 text-[var(--ink-muted)]">
            <span className="font-mono">{item.dispute_id}</span> · {reason(item.reason)} · capture <span className="font-mono">{item.capture_id ?? "—"}</span>{item.invoice_id ? <> · invoice <span className="font-mono">{item.invoice_id}</span></> : null}
          </p>
        </div>
        <div className="flex-none sm:text-right">
          <p className="num text-xl font-medium">{formatMoney(item.amount, item.currency)}</p>
          <p className="text-[13px] text-[var(--ink-muted)]">
            {item.workflow_status === "resolved" ? "Resolved" : <span className={hours !== null && hours < 0 ? "tone-negative" : undefined}>{dueLabel(item.seller_response_due)} to respond</span>}
          </p>
        </div>
      </div>

      <ol className="track mt-6" aria-label="Resolution progress">
        {steps.map((step) => (
          <li key={step.label} className="track-step" data-state={step.state} aria-current={step.state === "current" ? "step" : undefined}>
            <p className="track-label">{step.label}</p>
            <p className="track-meta">{step.meta}</p>
          </li>
        ))}
      </ol>

      {item.buyer_message && (
        <figure className="mt-6 rounded-lg bg-[var(--raised)] px-4 py-3">
          <blockquote className="text-sm leading-6">“{item.buyer_message}”</blockquote>
          <figcaption className="mt-1 text-xs text-[var(--ink-muted)]">Buyer, on PayPal. Treated as untrusted text by the agent.</figcaption>
        </figure>
      )}
    </div>
  )
}

function EvidenceBlock({ apiUrl, item, busy, disabled, onVerify }: { apiUrl: string; item: PayPalCase; busy: string | null; disabled: boolean; onVerify: (values: VerifyValues) => Promise<unknown> }) {
  const [dispatchImage, setDispatchImage] = useState<File | null>(null)
  const [returnImage, setReturnImage] = useState<File | null>(null)
  const [dispatchSku, setDispatchSku] = useState(item.item_sku ?? "")
  const [returnSku, setReturnSku] = useState(item.item_sku ?? "")
  const [dispatchWeight, setDispatchWeight] = useState("")
  const [returnWeight, setReturnWeight] = useState("")
  const [localError, setLocalError] = useState<string | null>(null)
  const dispatchPreview = usePreview(dispatchImage)
  const returnPreview = usePreview(returnImage)
  const verification = item.verification
  const meta = decisionMeta(verification?.risk.decision)
  const Icon = meta.icon
  const photosRequested = item.workflow_status === "awaiting_evidence" && Boolean(verification)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setLocalError(null)
    if (!dispatchImage || !returnImage) return setLocalError("Add both the dispatch photo and the returned-item photo.")
    if (!dispatchSku.trim() || !returnSku.trim()) return setLocalError("Enter both SKUs.")
    await onVerify({ dispatchImage, returnImage, dispatchSku, returnSku, dispatchWeight, returnWeight })
  }

  const form = (
    <form onSubmit={submit} className="space-y-4">
      <div className="grid gap-4 md:grid-cols-2">
        <UploadCard id={`dispatch-${item.case_id}`} title="Dispatch photo" subtitle="Taken when the order was packed" file={dispatchImage} preview={dispatchPreview} onChange={setDispatchImage} />
        <UploadCard id={`return-${item.case_id}`} title="Returned item photo" subtitle="Taken when the parcel came back" file={returnImage} preview={returnPreview} onChange={setReturnImage} />
      </div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Field id={`dsku-${item.case_id}`} label="Dispatch SKU" value={dispatchSku} onChange={setDispatchSku} placeholder="SKU" />
        <Field id={`rsku-${item.case_id}`} label="Return SKU" value={returnSku} onChange={setReturnSku} placeholder="SKU" />
        <Field id={`dw-${item.case_id}`} label="Dispatch weight (g)" value={dispatchWeight} onChange={setDispatchWeight} placeholder="Optional" type="number" />
        <Field id={`rw-${item.case_id}`} label="Return weight (g)" value={returnWeight} onChange={setReturnWeight} placeholder="Optional" type="number" />
      </div>
      {localError && <p role="alert" className="notice negative">{localError}</p>}
      <div className="flex flex-col-reverse justify-between gap-3 sm:flex-row sm:items-center">
        <p className="text-xs text-[var(--ink-muted)]">The first run loads the models and can take about 3 minutes.</p>
        <Button type="submit" variant={verification ? "outline" : "default"} disabled={busy !== null}>
          {busy === "verify" ? <><LoaderCircle className="animate-spin" /> Verifying…</> : <><ScanLine /> {verification ? "Verify again" : "Verify returned item"}</>}
        </Button>
      </div>
    </form>
  )

  return (
    <div className="panel p-6">
      <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start">
        <div>
          <h3 className="section-title">Evidence</h3>
          <p className="text-xs text-[var(--ink-muted)]">Dispatch photo against the item that came back: DINOv2, Qwen3-VL, calibrated fusion and the evidence auditor.</p>
        </div>
        {verification && <div className={`risk-banner ${meta.tone}`}><Icon className="size-4" /><span>{meta.label}</span><strong>{verification.risk.score}</strong></div>}
      </div>

      {verification && (
        <div className="mt-5 space-y-4">
          <dl className="grid grid-cols-2 gap-4 rounded-lg bg-[var(--raised)] p-4 sm:grid-cols-3 2xl:grid-cols-5">
            <Stat label="Calibrated probability" value={percent(verification.risk.probability)} />
            <Stat label="Visual similarity" value={percent(verification.features.vision_similarity)} />
            <Stat label="VLM mismatch" value={percent(verification.features.vlm_mismatch)} />
            <Stat label="Image quality" value={percent(verification.features.image_quality)} warn={verification.risk.decision === "recapture"} />
            <Stat label="Auditor" value={verification.auditor_assessment.recommendation_support.replaceAll("_", " ")} />
          </dl>
          {verification.vlm_assessment.observations.length > 0 && (
            <table className="w-full text-sm">
              <thead><tr className="text-left text-xs text-[var(--ink-muted)]"><th className="pb-2 font-normal">Attribute</th><th className="pb-2 font-normal">Dispatched</th><th className="pb-2 font-normal">Returned</th><th className="pb-2 text-right font-normal">Severity</th></tr></thead>
              <tbody>
                {verification.vlm_assessment.observations.map((obs, index) => (
                  <tr key={index} className="border-t border-[var(--rule)]">
                    <td className="py-2 pr-3 font-medium">{obs.attribute.replaceAll("_", " ")}</td>
                    <td className="py-2 pr-3 text-[var(--ink-muted)]">{obs.dispatch_value}</td>
                    <td className="py-2 pr-3">{obs.return_value}</td>
                    <td className="py-2 text-right"><span className={`pill ${obs.severity === "material" ? "negative" : "neutral"}`}>{obs.severity}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="flex flex-col justify-between gap-2 sm:flex-row sm:items-center">
            <p className="text-sm">{verification.auditor_assessment.reviewer_summary}</p>
            {item.has_report && <a href={reportUrl(apiUrl, item.case_id)} target="_blank" rel="noreferrer" className="inline-flex flex-none items-center gap-1.5 text-sm text-[var(--brand)] hover:underline"><FileDown className="size-4" /> Evidence report (PDF)</a>}
          </div>
        </div>
      )}

      {!disabled && (verification && !photosRequested ? (
        <details className="mt-5 border-t border-[var(--rule)] pt-4">
          <summary className="cursor-pointer text-sm font-medium">Verify with new photos</summary>
          <div className="mt-4">{form}</div>
        </details>
      ) : (
        <div className="mt-5">
          {photosRequested && <p className="notice warning mb-4">You asked the buyer for clearer photos. Verify again when they arrive.</p>}
          {form}
        </div>
      ))}
    </div>
  )
}

function Stat({ label, value, warn = false }: { label: string; value: string; warn?: boolean }) {
  return <div><dt className="text-xs text-[var(--ink-muted)]">{label}</dt><dd className={`num mt-0.5 text-sm font-medium capitalize ${warn ? "tone-warning" : ""}`}>{value}</dd></div>
}

function ResolutionBlock({ item, busy, disabled, onDraft, onExecute }: { item: PayPalCase; busy: string | null; disabled: boolean; onDraft: () => Promise<unknown>; onExecute: (body: ExecuteBody) => Promise<unknown> }) {
  const draft = item.draft
  const [action, setAction] = useState<ResolutionAction | null>(draft?.proposed_action ?? null)
  const [amount, setAmount] = useState(draft?.refund_amount ?? "")
  const [buyerMessage, setBuyerMessage] = useState(draft?.buyer_message ?? "")
  const [notes, setNotes] = useState(draft?.evidence_notes ?? "")
  const [reviewer, setReviewer] = useState("")

  const refundAmount = action === "refund_full" ? item.captured_amount ?? item.amount : action === "refund_partial" ? amount : null
  const summary = !action ? ""
    : action === "submit_evidence" ? `Send your evidence notes and the PDF report to PayPal for dispute ${item.dispute_id}. PayPal then decides the case.`
      : action === "request_photos" ? `Send the message to the buyer on dispute ${item.dispute_id}, asking for clearer photos.`
        : `Refund ${formatMoney(refundAmount, item.currency)} to the buyer on PayPal capture ${item.capture_id}. The money leaves the connected PayPal account.`

  return (
    <div className="panel p-6">
      <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start">
        <div>
          <h3 className="section-title">Resolution</h3>
          <p className="text-xs text-[var(--ink-muted)]">The agent drafts the PayPal response. It cannot call PayPal; you confirm.</p>
        </div>
        {draft && <span className={`pill ${draft.source === "llm_api" ? "brand" : "neutral"}`}>{draft.source === "llm_api" ? `Drafted by ${draft.model}` : "Drafted by policy rules"}</span>}
      </div>

      {!draft ? (
        <div className="mt-5 flex flex-col items-start justify-between gap-3 sm:flex-row sm:items-center">
          <p className="text-sm text-[var(--ink-muted)]">Turn the evidence and the dispute into a proposed PayPal action.</p>
          <Button onClick={() => void onDraft()} disabled={busy !== null || disabled}>{busy === "draft" ? <><LoaderCircle className="animate-spin" /> Drafting…</> : <><Sparkles /> Draft resolution</>}</Button>
        </div>
      ) : (
        <div className="mt-5 space-y-5">
          <p className="text-sm leading-6"><span className="text-[var(--ink-muted)]">Why: </span>{draft.rationale}</p>

          <fieldset>
            <legend className="text-sm font-medium">Action</legend>
            <p className="text-xs text-[var(--ink-muted)]">Only the actions this evidence allows are offered.</p>
            <div className="mt-2 flex flex-wrap gap-2" role="radiogroup">
              {draft.allowed_actions.map((option) => (
                <Button key={option} type="button" size="sm" role="radio" aria-checked={action === option} variant={action === option ? "default" : "outline"} onClick={() => setAction(option)} disabled={disabled}>
                  {ACTION_LABELS[option]}{option === draft.proposed_action ? " (proposed)" : ""}
                </Button>
              ))}
            </div>
          </fieldset>

          {action === "refund_partial" && (
            <div className="max-w-xs"><Label htmlFor="partial-amount">Refund amount ({item.currency})</Label><Input id="partial-amount" className="num mt-2" type="number" min={0} step="0.01" value={amount} onChange={(event) => setAmount(event.target.value)} disabled={disabled} aria-describedby="partial-hint" /><p id="partial-hint" className="mt-1 text-xs text-[var(--ink-muted)]">Below the captured {formatMoney(item.captured_amount, item.currency)}.</p></div>
          )}
          {action && action !== "submit_evidence" && (
            <div><Label htmlFor="buyer-message">Message to the buyer</Label><Textarea id="buyer-message" className="mt-2" rows={3} maxLength={600} value={buyerMessage} onChange={(event) => setBuyerMessage(event.target.value)} disabled={disabled} /></div>
          )}
          {action === "submit_evidence" && (
            <div><Label htmlFor="evidence-notes">Evidence notes for PayPal</Label><Textarea id="evidence-notes" className="mt-2" rows={5} maxLength={1200} value={notes} onChange={(event) => setNotes(event.target.value)} disabled={disabled} /><p className="mt-1 text-xs text-[var(--ink-muted)]">The evidence report PDF is attached automatically.</p></div>
          )}

          {!disabled && (
            <div className="flex flex-col gap-3 border-t border-[var(--rule)] pt-5 sm:flex-row sm:items-end sm:justify-between">
              <div className="w-full max-w-xs"><Label htmlFor="reviewer">Your name</Label><Input id="reviewer" className="mt-2" value={reviewer} onChange={(event) => setReviewer(event.target.value)} placeholder="Recorded with the action" /></div>
              <div className="flex gap-2">
                <Button variant="ghost" onClick={() => void onDraft()} disabled={busy !== null}><RefreshCw /> Redraft</Button>
                <AlertDialog>
                  <AlertDialogTrigger asChild>
                    <Button disabled={!action || reviewer.trim().length < 2 || busy !== null}>{busy === "execute" ? <><LoaderCircle className="animate-spin" /> Sending…</> : "Review and send to PayPal"}</Button>
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>{action ? ACTION_LABELS[action] : ""}</AlertDialogTitle>
                      <AlertDialogDescription>{summary} Confirming as {reviewer.trim()}; your name goes into the case log, and confirming twice never sends it twice.</AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>Cancel</AlertDialogCancel>
                      <AlertDialogAction onClick={() => action && void onExecute({ action, reviewer_name: reviewer.trim(), confirm: true, refund_amount: action === "refund_partial" ? amount : null, buyer_message: action === "submit_evidence" ? "" : buyerMessage, evidence_notes: action === "submit_evidence" ? notes : "" })}>
                        Send to PayPal
                      </AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

const ACTIVITY_LABELS: Record<string, string> = {
  webhook: "Dispute received from PayPal",
  verified: "Returned item verified",
  drafted: "Resolution drafted",
}

function activityLabel(entry: CaseAction) {
  if (entry.action.startsWith("executed:")) return `Sent to PayPal: ${ACTION_LABELS[entry.action.slice(9) as ResolutionAction] ?? entry.action.slice(9)}`
  if (entry.action.startsWith("failed:")) return `PayPal rejected: ${ACTION_LABELS[entry.action.slice(7) as ResolutionAction] ?? entry.action.slice(7)}`
  return ACTIVITY_LABELS[entry.action] ?? entry.action
}

function activityDetail(entry: CaseAction) {
  const d = entry.detail
  if (entry.action === "verified") return `${decisionMeta(String(d.decision)).label}, score ${d.score}`
  if (entry.action === "drafted") return `Proposed ${ACTION_LABELS[d.proposed_action as ResolutionAction] ?? d.proposed_action}`
  if (entry.action.startsWith("failed:")) return `${d.error ?? "Error"}${d.debug_id ? ` · debug ${d.debug_id}` : ""}`
  if (entry.action.startsWith("executed:")) {
    const parts = [d.paypal_refund_id ? `refund ${d.paypal_refund_id}` : null, d.amount ? `${d.amount} ${d.currency ?? ""}`.trim() : null, d.paypal_status ? String(d.paypal_status).toLowerCase().replaceAll("_", " ") : null, d.replayed ? "replayed" : null]
    return parts.filter(Boolean).join(" · ")
  }
  return null
}

function ActivityBlock({ actions }: { actions: CaseAction[] }) {
  if (actions.length === 0) return null
  return (
    <div className="panel p-6">
      <h3 className="section-title">Activity</h3>
      <p className="text-xs text-[var(--ink-muted)]">Every system, agent and reviewer step, in order. Nothing is deleted.</p>
      <ol className="mt-4">
        {actions.map((entry, index) => {
          const failed = entry.action.startsWith("failed:")
          const detail = activityDetail(entry)
          return (
            <li key={index} className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 border-t border-[var(--rule)] py-2.5 first:border-t-0">
              <p className={`text-sm ${failed ? "tone-negative" : ""}`}>{activityLabel(entry)}</p>
              <p className="num text-right text-[13px] text-[var(--ink-muted)]">{clock(entry.created_at)}</p>
              <p className="truncate text-xs text-[var(--ink-muted)]">{entry.actor.replace("reviewer:", "by ").replace("resolution_agent:", "agent · ").replaceAll("_", " ")}{detail ? ` · ${detail}` : ""}</p>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
