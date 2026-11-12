"use client"

import { useEffect, useMemo, useState } from "react"
import {
  AlertTriangle, Braces, Check, CheckCircle2, CircleDot, Cpu, Gauge, Gavel, ImageOff,
  ListChecks, Menu, ScanLine, ShieldCheck, Sparkles, Workflow,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import { Progress } from "@/components/ui/progress"
import { Slider } from "@/components/ui/slider"
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { LiveVerification } from "@/components/live-verification"
import { PayPalDisputes } from "@/components/paypal-disputes"
import heldout from "@/evaluation/results/heldout-v2.json"
import selection from "@/evaluation/results/selection-v2.json"
import realReport from "@/evaluation/results/real-report.json"
import { DEMO } from "@/lib/demo"
import { defaultApiUrl, fetchHealth, type HealthResponse } from "@/lib/live-api"
import { evaluateSyntheticBaseline, returnCases, scoreReturn, type ReviewDecision } from "@/lib/swapshield"

const money = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 })

type View = "disputes" | "live" | "queue" | "evaluation" | "pipeline"

const NAV: Array<{ group: string; items: Array<{ id: View; label: string; icon: typeof Gavel }> }> = [
  { group: "Resolve", items: [{ id: "disputes", label: "Disputes", icon: Gavel }, { id: "live", label: "Live verify", icon: ScanLine }] },
  { group: "Model", items: [{ id: "queue", label: "Benchmark queue", icon: ListChecks }, { id: "evaluation", label: "Evaluation", icon: Gauge }, { id: "pipeline", label: "Model pipeline", icon: Workflow }] },
]

function decisionMeta(decision: ReviewDecision) {
  if (decision === "approve") return { label: "Approve refund", tone: "safe", icon: CheckCircle2 }
  if (decision === "recapture") return { label: "Request recapture", tone: "uncertain", icon: ImageOff }
  return { label: "Human review", tone: "risk", icon: AlertTriangle }
}

function Mark() {
  return (
    <svg viewBox="0 0 24 24" className="size-6 flex-none" aria-hidden="true">
      <path d="M12 2.5 4 5.5v6.2c0 4.6 3.3 8.6 8 9.8 4.7-1.2 8-5.2 8-9.8V5.5l-8-3Z" fill="var(--brand)" />
      <path d="m8.4 12.2 2.4 2.4 4.8-5" fill="none" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export default function Home() {
  const [view, setView] = useState<View>("disputes")
  const [menuOpen, setMenuOpen] = useState(false)
  const [openDisputes, setOpenDisputes] = useState<number | null>(null)
  const [health, setHealth] = useState<HealthResponse | null>(null)

  useEffect(() => {
    let active = true
    if (DEMO) return
    void fetchHealth(defaultApiUrl).then((next) => active && setHealth(next), () => undefined)
    return () => {
      active = false
    }
  }, [])

  const paypalMode = DEMO ? "demo" : health?.paypal?.configured ? health.paypal.mode : health ? "not connected" : "…"

  function go(next: View) {
    setView(next)
    setMenuOpen(false)
  }

  const nav = (
    <nav aria-label="Screens" className="flex h-full flex-col">
      <div className="flex items-center gap-2.5 px-2.5 pt-1">
        <Mark />
        <div className="min-w-0">
          <p className="text-[15px] font-semibold leading-5">SwapShield</p>
          <p className="truncate text-xs text-[var(--ink-muted)]">Demo store · PayPal {paypalMode}</p>
        </div>
      </div>
      {NAV.map((group) => (
        <div key={group.group}>
          <p className="nav-group">{group.group}</p>
          <div className="space-y-0.5">
            {group.items.map((item) => (
              <button key={item.id} type="button" className="nav-item" aria-current={view === item.id ? "page" : undefined} onClick={() => go(item.id)}>
                <item.icon aria-hidden="true" />
                {item.label}
                {item.id === "disputes" && openDisputes ? <span className="nav-count" aria-label={`${openDisputes} disputes need a response`}>{openDisputes}</span> : null}
              </button>
            ))}
          </div>
        </div>
      ))}
      <p className="mt-auto px-2.5 pb-1 text-xs leading-5 text-[var(--ink-muted)]">Money moves only after a named reviewer confirms. PayPal decides contested cases.</p>
    </nav>
  )

  return (
    <div className="app-shell">
      <aside className="sticky top-0 hidden h-screen px-2.5 py-4 lg:block">{nav}</aside>

      <div className="app-sheet">
        <div className="flex h-12 items-center gap-2 border-b border-[var(--hairline)] bg-[var(--frame)] px-4 lg:hidden">
          <Sheet open={menuOpen} onOpenChange={setMenuOpen}>
            <SheetTrigger asChild>
              <Button variant="ghost" size="icon" aria-label="Open navigation"><Menu /></Button>
            </SheetTrigger>
            <SheetContent side="left" className="w-[280px] bg-[var(--frame)] px-2.5 py-4">
              <SheetTitle className="sr-only">Navigation</SheetTitle>
              {nav}
            </SheetContent>
          </Sheet>
          <Mark />
          <span className="text-sm font-semibold">SwapShield</span>
        </div>

        <main className="app-content">
          {DEMO && (
            <p className="notice neutral mb-6 text-sm">
              <strong className="font-medium">Demo mode.</strong> Every verification and agent proposal here was recorded from the real SwapShield API (GPU models, mock PayPal). Confirming applies the same safety rules but sends nothing to PayPal.{" "}
              <a className="text-[var(--brand)] hover:underline" href="https://github.com/sidddha2004/swapshield-paypal#try-it" target="_blank" rel="noreferrer">Run it locally</a> for live verification and the PayPal sandbox.
            </p>
          )}
          {view === "disputes" && <PayPalDisputes onOpenCountChange={setOpenDisputes} />}
          {view === "live" && <LiveView />}
          {view === "queue" && <BenchmarkQueue />}
          {view === "evaluation" && <EvaluationView />}
          {view === "pipeline" && <PipelineView />}
        </main>
      </div>
    </div>
  )
}

function TitleRow({ title, line, aside }: { title: string; line: string; aside?: React.ReactNode }) {
  return (
    <div className="mb-6 flex flex-col justify-between gap-3 sm:flex-row sm:items-end">
      <div>
        <h1 className="page-title">{title}</h1>
        <p className="mt-1 max-w-3xl text-sm text-[var(--ink-muted)]">{line}</p>
      </div>
      {aside}
    </div>
  )
}

function LiveView() {
  return (
    <>
      <TitleRow title="Live verify" line="Run the verifier on any dispatch and return photo pair on the local GPU. Nothing is sent to PayPal from here." />
      {DEMO ? (
        <div className="panel p-6 text-sm leading-6">
          <p>Live verify runs DINOv2 and Qwen3-VL on your own photos, so it needs the SwapShield API on a machine with a GPU, which this hosted demo does not have.</p>
          <p className="mt-2 text-[var(--ink-muted)]">Open <strong className="font-medium text-[var(--ink)]">Disputes</strong> to replay three real verifications, or follow the README to run everything locally.</p>
        </div>
      ) : (
        <LiveVerification />
      )}
    </>
  )
}

function BenchmarkQueue() {
  const [selectedId, setSelectedId] = useState(returnCases[0].id)
  const [recordedDecision, setRecordedDecision] = useState<ReviewDecision | null>(null)
  const threshold = 68
  const selected = returnCases.find((item) => item.id === selectedId) ?? returnCases[0]
  const result = scoreReturn(selected.features, threshold / 100)
  const recommended = decisionMeta(result.decision)
  const RecommendedIcon = recommended.icon

  return (
    <>
      <TitleRow title="Benchmark queue" line={`${returnCases.length} seeded returns that illustrate the decision flow. Scores come from the demo scoring function, not live model output.`} />
      <section className="grid gap-6 xl:grid-cols-[340px_minmax(0,1fr)]">
        <aside className="panel self-start p-2">
          {returnCases.map((item) => {
            const itemResult = scoreReturn(item.features, threshold / 100)
            const meta = decisionMeta(itemResult.decision)
            return (
              <button key={item.id} type="button" className="queue-row" aria-current={selected.id === item.id} onClick={() => { setSelectedId(item.id); setRecordedDecision(null) }}>
                <div className="flex items-baseline justify-between gap-3">
                  <span className="truncate text-sm font-medium">{item.product}</span>
                  <span className="num text-sm">{money.format(item.amount)}</span>
                </div>
                <div className="mt-1 flex items-center justify-between gap-3 text-xs text-[var(--ink-muted)]">
                  <span className="truncate"><span className="font-mono">{item.id}</span> · {item.customer}</span>
                  <span className={`decision-text ${meta.tone} flex-none`}>{meta.label} · <span className="num">{itemResult.score}</span></span>
                </div>
              </button>
            )
          })}
        </aside>

        <div className="space-y-6">
          <section className="panel p-6">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
              <div>
                <h2 className="text-lg font-semibold">{selected.product}</h2>
                <p className="mt-1 text-sm text-[var(--ink-muted)]"><span className="font-mono">{selected.id}</span> · {selected.orderId} · {selected.category} · {money.format(selected.amount)} · received {selected.receivedAt}</p>
              </div>
              <div className={`risk-banner ${recommended.tone}`}><RecommendedIcon className="size-4" /><span>{recommended.label}</span><strong>{result.score}</strong></div>
            </div>
            <div className="mt-6 grid gap-6 sm:grid-cols-2 2xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_260px]">
              <EvidenceCard title="Dispatch record" subtitle="Captured at fulfilment" sku={selected.outbound.sku} serial={selected.outbound.serial} weight={selected.outbound.weightGrams} code="Outbound" />
              <EvidenceCard title="Returned item" subtitle="Warehouse capture" sku={selected.returned.sku} serial={selected.returned.serial} weight={selected.returned.weightGrams} code="Return" />
              <div className="rounded-lg bg-[var(--raised)] p-4 sm:col-span-2 2xl:col-span-1">
                <p className="text-xs text-[var(--ink-muted)]">Risk score</p>
                <p className="num mt-2 text-[28px] leading-8">{result.score}<span className="text-sm text-[var(--ink-muted)]"> /100</span></p>
                <Progress value={result.score} className={`mt-4 progress-${recommended.tone}`} />
                <p className="mt-3 text-xs leading-5 text-[var(--ink-muted)]">Review threshold {threshold}. Low-quality evidence always abstains.</p>
              </div>
            </div>
          </section>

          <section className="grid gap-6 2xl:grid-cols-[minmax(0,1.1fr)_minmax(320px,0.9fr)]">
            <div className="panel overflow-hidden">
              <div className="px-5 pt-4 pb-2"><h3 className="section-title">Evidence</h3><p className="text-xs text-[var(--ink-muted)]">Every finding names its source</p></div>
              <Table>
                <TableHeader><TableRow><TableHead className="pl-5">Signal</TableHead><TableHead>Result</TableHead><TableHead className="pr-5">Source</TableHead></TableRow></TableHeader>
                <TableBody>
                  {selected.evidence.map((signal) => (
                    <TableRow key={signal.label}><TableCell className="pl-5 font-medium">{signal.label}</TableCell><TableCell><span className={`signal-badge ${signal.strength}`}>{signal.value}</span></TableCell><TableCell className="pr-5 font-mono text-xs text-[var(--ink-muted)]">{signal.source}</TableCell></TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>

            <div className="panel p-5">
              <h3 className="section-title">Investigator note</h3>
              <p className="mt-2 text-sm leading-6">
                {result.decision === "recapture" ? "The current images are insufficient for a reliable decision. Request a clear rear-label image before continuing." : result.decision === "review" ? "The returned item has several independently verified mismatches. Hold the refund and send this case to a human reviewer." : "The returned item is consistent with the dispatch record. No material mismatch is supported by the available evidence."}
              </p>
              <ul className="mt-3 space-y-1.5">
                {result.reasons.slice(0, 3).map((reason) => <li key={reason} className="flex gap-2 text-xs leading-5 text-[var(--ink-muted)]"><CircleDot className="mt-1 size-3 flex-none" />{reason}</li>)}
              </ul>
              <div className="mt-5 flex flex-wrap gap-2">
                <Button variant={result.decision === "approve" ? "default" : "outline"} size="sm" onClick={() => setRecordedDecision("approve")}>Approve</Button>
                <Button variant={result.decision === "recapture" ? "default" : "outline"} size="sm" onClick={() => setRecordedDecision("recapture")}>Recapture</Button>
                <Button variant={result.decision === "review" ? "default" : "outline"} size="sm" onClick={() => setRecordedDecision("review")}>Review</Button>
              </div>
              {recordedDecision && <p className="mt-3 flex items-center gap-2 text-xs"><Check className="size-3 tone-positive" /> Decision recorded for this browser session: {recordedDecision}.</p>}
            </div>
          </section>
        </div>
      </section>
    </>
  )
}

function EvaluationView() {
  const [demoThreshold, setDemoThreshold] = useState(68)
  const syntheticEvaluation = useMemo(() => evaluateSyntheticBaseline(demoThreshold / 100), [demoThreshold])
  const lockedTest = realReport.test
  const shipped = heldout.methods.fusion

  return (
    <>
      <TitleRow
        title="Evaluation"
        line={`Shipped: trained fusion at threshold ${selection.chosen_threshold}, chosen on ${selection.validation_cases} validation pairs. On ${selection.test_cases} unseen test pairs it catches ${(selection.test.recall * 100).toFixed(0)}% of swaps and sends ${selection.test.confusion.fp} of ${heldout.test_genuine} genuine returns to a person first.`}
        aside={<span className="pill neutral">Item-disjoint · thresholds from validation</span>}
      />
      <section className="space-y-6">
        <div className="panel overflow-hidden">
          <div className="px-5 pt-4 pb-2">
            <h2 className="section-title">Model selection on validation v2</h2>
            <p className="text-xs text-[var(--ink-muted)]">Rule committed before the run: lowest expected cost on {selection.validation_cases} new validation pairs (₹80 per false flag, ₹6,200 per missed swap). The test results had been seen before; this is disclosed.</p>
          </div>
          <Table>
            <TableHeader><TableRow><TableHead className="pl-5">Method</TableHead><TableHead className="text-right">Threshold</TableHead><TableHead className="text-right">Validation cost</TableHead><TableHead className="text-right">False flags</TableHead><TableHead className="pr-5 text-right">Missed swaps</TableHead></TableRow></TableHeader>
            <TableBody>
              {(["fusion", "vlm", "dinov2"] as const).map((key) => {
                const c = selection.candidates[key]
                return (
                  <TableRow key={key}>
                    <TableCell className="pl-5 font-medium">{c.label}{key === selection.chosen && <span className="pill positive ml-2">Shipped</span>}</TableCell>
                    <TableCell className="num text-right">{c.validation_threshold.toFixed(2)}</TableCell>
                    <TableCell className="num text-right">₹{c.validation_cost.toLocaleString("en-IN")}</TableCell>
                    <TableCell className="num text-right">{c.validation_confusion.fp}</TableCell>
                    <TableCell className="num pr-5 text-right">{c.validation_confusion.fn}</TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
          <p className="notice neutral m-5 mt-3 text-xs leading-5">On the {selection.test_cases} test pairs at threshold {selection.chosen_threshold}: precision {selection.test.precision.toFixed(3)}, recall {selection.test.recall.toFixed(3)}, F1 {selection.test.f1.toFixed(3)} ({selection.test.bootstrap_95_ci.f1[0].toFixed(3)}–{selection.test.bootstrap_95_ci.f1[1].toFixed(3)}). A missed swap is declared 77 times costlier than a false flag, so the threshold favours catching swaps; &ldquo;review&rdquo; means a person looks before any refund, never a refusal.</p>
        </div>

        <div className="panel overflow-hidden">
          <div className="px-5 pt-4 pb-2">
            <h2 className="section-title">Held-out benchmark v2 at the v1 thresholds</h2>
            <p className="text-xs text-[var(--ink-muted)]">{heldout.test_cases} pairs ({heldout.test_genuine} genuine, {heldout.test_substitution} substitutions) whose products appear nowhere in the v1 data. Each method&apos;s threshold was chosen on v1 validation; the test was scored once.</p>
          </div>
          <Table>
            <TableHeader><TableRow><TableHead className="pl-5">Method</TableHead><TableHead className="text-right">Precision</TableHead><TableHead className="text-right">Recall</TableHead><TableHead className="text-right">F1</TableHead><TableHead className="text-right">95% CI</TableHead><TableHead className="text-right">False flags</TableHead><TableHead className="pr-5 text-right">Missed swaps</TableHead></TableRow></TableHeader>
            <TableBody>
              {(["fusion", "vlm", "dinov2"] as const).map((key) => {
                const m = heldout.methods[key]
                return (
                  <TableRow key={key}>
                    <TableCell className="pl-5 font-medium">{m.label}</TableCell>
                    <TableCell className="num text-right">{m.precision.toFixed(3)}</TableCell>
                    <TableCell className="num text-right">{m.recall.toFixed(3)}</TableCell>
                    <TableCell className="num text-right font-medium">{m.f1.toFixed(3)}</TableCell>
                    <TableCell className="num text-right text-[var(--ink-muted)]">{m.intervals.f1[0].toFixed(3)}–{m.intervals.f1[1].toFixed(3)}</TableCell>
                    <TableCell className="num text-right">{m.confusion.fp}<span className="text-[var(--ink-muted)]"> /{heldout.test_genuine}</span></TableCell>
                    <TableCell className={`num pr-5 text-right ${key === "fusion" ? "tone-negative" : ""}`}>{m.confusion.fn}<span className="text-[var(--ink-muted)]"> /{heldout.test_substitution}</span></TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
          <p className="notice neutral m-5 mt-3 text-xs leading-5">The trained fusion over-weights visual similarity, the weakest signal, so it misses more swaps than the vision-language score alone. The shipped method was then chosen on separate validation data, above, rather than from this table. {(shipped.recapture_rate * 100).toFixed(0)}% of these studio images fell under the photo-quality gate, which needs recalibrating on real merchant photos.</p>
        </div>

        <h2 className="section-title">Locked v1 test (30 pairs)</h2>
        <div className="panel grid grid-cols-2 lg:grid-cols-4">
          <Metric label="Precision" value={`${(lockedTest.precision * 100).toFixed(1)}%`} note="95% CI 100–100%" />
          <Metric label="Recall" value={`${(lockedTest.recall * 100).toFixed(1)}%`} note="95% CI 66.7–100%" />
          <Metric label="F1 score" value={lockedTest.f1.toFixed(3)} note="95% CI 0.800–1.000" />
          <Metric label="Locked test pairs" value={lockedTest.cases.toString()} note="15 genuine · 15 substitutions" />
        </div>

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
          <div className="space-y-6">
            <div className="panel p-6">
              <div className="flex flex-col justify-between gap-2 sm:flex-row sm:items-start">
                <div><h2 className="section-title">Outcome audit</h2><p className="text-xs text-[var(--ink-muted)]">No false review flags; two substitutions were missed.</p></div>
                <span className="text-xs text-[var(--ink-muted)]">PR average precision <span className="num text-[var(--ink)]">0.996</span></span>
              </div>
              <div className="mt-5 grid gap-6 lg:grid-cols-[minmax(0,1fr)_240px]">
                <div className="grid grid-cols-2 gap-3">
                  <ConfusionCell label="True substitution" value={lockedTest.confusion.tp} note="Detected" tone="safe" />
                  <ConfusionCell label="Missed substitution" value={lockedTest.confusion.fn} note="False negative" tone="risk" />
                  <ConfusionCell label="Genuine cleared" value={lockedTest.confusion.tn} note="True negative" tone="safe" />
                  <ConfusionCell label="False review flag" value={lockedTest.confusion.fp} note="False positive" tone="neutral" />
                </div>
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-1">
                  <CostStat label="False-positive cost" value={money.format(lockedTest.false_positive_cost)} cost="0 genuine cases flagged" />
                  <CostStat label="Missed-swap loss" value={money.format(lockedTest.missed_substitution_loss)} cost="2 × ₹6,200" />
                </div>
              </div>
              <div className="mt-5 grid gap-4 border-t border-[var(--rule)] pt-5 sm:grid-cols-2 lg:grid-cols-4">
                <MiniStat label="Calibration error" value={lockedTest.calibration_error.toFixed(3)} />
                <MiniStat label="Recapture rate" value={`${(lockedTest.recapture_rate * 100).toFixed(1)}%`} />
                <MiniStat label="Genuine recapture" value={`${(lockedTest.genuine_recapture_rate * 100).toFixed(1)}%`} />
                <MiniStat label="Latency p50 / p95" value="11.5 s / 65.4 s" />
              </div>
            </div>

            <div className="panel overflow-hidden">
              <div className="px-5 pt-4 pb-2"><h2 className="section-title">Category slices</h2><p className="text-xs text-[var(--ink-muted)]">The lamp slice holds both misses; small slices should not be overgeneralised.</p></div>
              <Table>
                <TableHeader><TableRow><TableHead className="pl-5">Category</TableHead><TableHead className="text-right">Cases</TableHead><TableHead className="text-right">TP / FN</TableHead><TableHead className="text-right">Precision</TableHead><TableHead className="text-right">Recall</TableHead><TableHead className="pr-5 text-right">F1</TableHead></TableRow></TableHeader>
                <TableBody>
                  <CategoryRow name="Chair" result={lockedTest.slices["category:chair"]} />
                  <CategoryRow name="Lamp" result={lockedTest.slices["category:lamp"]} warning />
                  <CategoryRow name="Sofa" result={lockedTest.slices["category:sofa"]} />
                  <CategoryRow name="Table" result={lockedTest.slices["category:table"]} />
                </TableBody>
              </Table>
            </div>

            <div className="panel p-6">
              <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start">
                <div><h2 className="section-title">Threshold sandbox</h2><p className="text-xs text-[var(--ink-muted)]">Cost sensitivity on 480 generated pairs. This never changes the locked result above.</p></div>
                <span className="pill warning">Synthetic data</span>
              </div>
              <div className="mt-6 grid gap-8 lg:grid-cols-[minmax(0,1fr)_240px]">
                <div>
                  <div className="flex items-end justify-between">
                    <div><p className="text-xs text-[var(--ink-muted)]">Demo threshold</p><p className="num mt-1 text-[28px] leading-8">{demoThreshold}</p></div>
                    <p className="num text-right text-xs leading-5 text-[var(--ink-muted)]">Precision {(syntheticEvaluation.precision * 100).toFixed(1)}%<br />Recall {(syntheticEvaluation.recall * 100).toFixed(1)}%</p>
                  </div>
                  <Slider className="mt-6" min={40} max={90} step={1} value={[demoThreshold]} onValueChange={(value) => setDemoThreshold(value[0])} aria-label="Demo threshold" />
                  <div className="mt-3 flex justify-between text-xs text-[var(--ink-muted)]"><span>More recall</span><span>Less friction</span></div>
                </div>
                <div className="grid grid-cols-2 gap-3"><CostStat label="False positives" value={syntheticEvaluation.falsePositives.toString()} cost={money.format(syntheticEvaluation.falsePositiveCost)} /><CostStat label="Missed swaps" value={syntheticEvaluation.falseNegatives.toString()} cost={money.format(syntheticEvaluation.missedLoss)} /></div>
              </div>
            </div>
          </div>

          <aside className="space-y-6">
            <div className="panel p-5">
              <h2 className="section-title">Reproducibility</h2>
              <p className="text-xs text-[var(--ink-muted)]">Protocol item-disjoint-v1</p>
              <div className="mt-4 space-y-3">
                <Gate title="Threshold from validation" detail="0.03 chosen on 304 validation pairs (v1: 0.92)" />
                <Gate title="Test exclusions disclosed" detail="2 prior smoke pairs removed" />
                <Gate title="Cost declared" detail="₹80 per false positive · ₹6,200 per miss" />
                <Gate title="Model card complete" detail="Limits and intended use documented" />
              </div>
            </div>
            <div className="panel p-5">
              <h2 className="section-title">Known limitations</h2>
              <ul className="mt-3 space-y-2.5 text-xs leading-5">
                <li className="flex gap-2"><AlertTriangle className="mt-0.5 size-3.5 flex-none tone-warning" />Furniture-only ABO benchmark; not production merchant traffic.</li>
                <li className="flex gap-2"><AlertTriangle className="mt-0.5 size-3.5 flex-none tone-warning" />Lamp recall is 50%; both false negatives are in this slice.</li>
                <li className="flex gap-2"><AlertTriangle className="mt-0.5 size-3.5 flex-none tone-warning" />40% recapture and 65.4 s p95 latency need improvement.</li>
                <li className="flex gap-2"><AlertTriangle className="mt-0.5 size-3.5 flex-none tone-warning" />ABO does not validate serial-number or weight signals.</li>
              </ul>
              <p className="notice neutral mt-4 text-xs leading-5">The ranking is strong, but the wide recall interval and small slices make this a credible proof of concept, not a production guarantee.</p>
            </div>
          </aside>
        </div>
      </section>
    </>
  )
}

function PipelineView() {
  return (
    <>
      <TitleRow title="Model pipeline" line="DINOv2 measures visual identity, Qwen3-VL extracts observable differences, and a trained fusion model makes the recommendation. An optional LLM audits consistency without changing the score." aside={<span className="pill positive">RTX 5050 smoke test passed</span>} />
      <section className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className="space-y-6">
          <div className="panel overflow-hidden">
            <div className="px-5 pt-4 pb-2"><h2 className="section-title">Stages</h2><p className="text-xs text-[var(--ink-muted)]">In the order a return passes through them</p></div>
            <Table>
              <TableHeader><TableRow><TableHead className="pl-5">Stage</TableHead><TableHead>Role</TableHead><TableHead>Runtime</TableHead><TableHead className="pr-5 text-right">Status</TableHead></TableRow></TableHeader>
              <TableBody>
                <PipelineStage icon={Cpu} name="DINOv2 small" role="Pair embedding similarity" runtime="Local CUDA" />
                <PipelineStage icon={Sparkles} name="Qwen3-VL 4B" role="Evidence-only comparison" runtime="4-bit · token-capped" />
                <PipelineStage icon={Braces} name="Schema + grounder" role="Reject extra fields and citations" runtime="Unit-tested" />
                <PipelineStage icon={ShieldCheck} name="Risk policy" role="Approve, recapture or review" runtime="Deterministic" />
                <PipelineStage icon={Gauge} name="LLM evidence auditor" role="Independent consistency check" runtime="Optional API · advisory" />
              </TableBody>
            </Table>
          </div>

          <div className="panel overflow-hidden">
            <div className="px-5 pt-4 pb-2"><h2 className="section-title">Implementation truth table</h2><p className="text-xs text-[var(--ink-muted)]">What is verified in code, on hardware and in the locked benchmark</p></div>
            <Table>
              <TableHeader><TableRow><TableHead className="pl-5">Layer</TableHead><TableHead>Status</TableHead><TableHead className="pr-5">Evidence</TableHead></TableRow></TableHeader>
              <TableBody>
                <ModelRow layer="Risk + abstention" status="Verified" evidence="Calibrated threshold plus explicit recapture gate" />
                <ModelRow layer="Structured VLM guard" status="Verified" evidence="Schema, grounding and OOM retry tests" />
                <ModelRow layer="Evidence auditor" status="Verified" evidence="Strict schema · API fallback · no score authority" />
                <ModelRow layer="PayPal resolution layer" status="Verified" evidence="22 workflow tests · mock and sandbox clients" />
                <ModelRow layer="Safety test suite" status="Verified" evidence="41 ML tests passing" />
                <ModelRow layer="Local API" status="Implemented" evidence="POST /v1/verify · lazy GPU load" />
                <ModelRow layer="Model execution" status="Verified" evidence="Real mouse pair · RTX 5050 · 4-bit VLM" />
                <ModelRow layer="Benchmark harness" status="Verified" evidence="Leakage, cost, calibration, CI and slices" />
                <ModelRow layer="Real-image benchmark" status="Verified" evidence="304 unseen pairs · 98% of swaps caught at the shipped threshold" />
              </TableBody>
            </Table>
          </div>
        </div>

        <aside className="panel h-fit p-5">
          <h2 className="section-title">Authority boundary</h2>
          <p className="text-xs text-[var(--ink-muted)]">Enforced in code, not by prompt alone</p>
          <div className="mt-4 space-y-3">
            <Gate title="No fraud accusation" detail="Only observable mismatches are allowed" />
            <Gate title="No autonomous rejection" detail="Adverse outcomes go to PayPal with evidence" />
            <Gate title="Prompt injection contained" detail="Buyer text, pixels and OCR stay untrusted data" />
            <Gate title="Money needs a named reviewer" detail="Idempotent PayPal call, logged with the name" />
          </div>
          <p className="notice neutral mt-5 text-xs leading-5">The first real mouse pair scored 95% same-product likelihood, and the policy still asked for recapture because both photos were under the sharpness gate: an intentional abstention.</p>
        </aside>
      </section>
    </>
  )
}

function EvidenceCard({ title, subtitle, sku, serial, weight, code }: { title: string; subtitle: string; sku: string; serial: string | null; weight: number; code: string }) {
  return (
    <div className="overflow-hidden rounded-lg border border-[var(--hairline)]">
      <div className="evidence-visual"><ScanLine className="size-7" aria-hidden="true" /><span className="evidence-code">{code} · {sku}</span></div>
      <div className="p-4">
        <div className="flex items-start justify-between gap-3"><div><p className="text-sm font-medium">{title}</p><p className="text-xs text-[var(--ink-muted)]">{subtitle}</p></div><span className="num text-sm">{weight}<span className="text-xs text-[var(--ink-muted)]"> g</span></span></div>
        <div className="mt-3 flex items-center justify-between border-t border-[var(--rule)] pt-3 text-xs"><span className="text-[var(--ink-muted)]">Serial</span><span className="font-mono">{serial ?? "—"}</span></div>
      </div>
    </div>
  )
}

function Metric({ label, value, note }: { label: string; value: string; note: string }) {
  return <div className="border-[var(--hairline)] p-5 [&:not(:first-child)]:border-l max-lg:[&:nth-child(3)]:border-l-0 max-lg:[&:nth-child(n+3)]:border-t"><p className="text-xs text-[var(--ink-muted)]">{label}</p><p className="num mt-2 text-[28px] leading-8">{value}</p><p className="mt-1 text-xs text-[var(--ink-muted)]">{note}</p></div>
}
function CostStat({ label, value, cost }: { label: string; value: string; cost: string }) {
  return <div className="rounded-lg bg-[var(--raised)] p-3"><p className="text-xs text-[var(--ink-muted)]">{label}</p><p className="num mt-1 text-xl font-medium">{value}</p><p className="mt-0.5 text-xs text-[var(--ink-muted)]">{cost}</p></div>
}
function MiniStat({ label, value }: { label: string; value: string }) {
  return <div><p className="text-xs text-[var(--ink-muted)]">{label}</p><p className="num mt-0.5 text-sm font-medium">{value}</p></div>
}
function ConfusionCell({ label, value, note, tone }: { label: string; value: number; note: string; tone: "safe" | "risk" | "neutral" }) {
  return <div className="rounded-lg bg-[var(--raised)] p-4"><p className="text-xs text-[var(--ink-muted)]">{label}</p><p className={`num mt-1 text-[28px] leading-8 ${tone === "risk" && value > 0 ? "tone-negative" : ""}`}>{value}</p><p className="text-xs text-[var(--ink-muted)]">{note}</p></div>
}
type CategoryResult = typeof realReport.test.slices["category:chair"]
function CategoryRow({ name, result, warning = false }: { name: string; result: CategoryResult; warning?: boolean }) {
  return <TableRow><TableCell className="pl-5 font-medium">{name}{warning && <span className="pill warning ml-2">Weak slice</span>}</TableCell><TableCell className="num text-right">{result.cases}</TableCell><TableCell className="num text-right">{result.confusion.tp} / {result.confusion.fn}</TableCell><TableCell className="num text-right">{(result.precision * 100).toFixed(0)}%</TableCell><TableCell className={`num text-right ${warning ? "tone-warning" : ""}`}>{(result.recall * 100).toFixed(0)}%</TableCell><TableCell className="num pr-5 text-right">{result.f1.toFixed(3)}</TableCell></TableRow>
}
function Gate({ title, detail }: { title: string; detail: string }) {
  return <div className="flex gap-2.5"><CheckCircle2 className="mt-0.5 size-4 flex-none tone-positive" aria-hidden="true" /><div><p className="text-sm">{title}</p><p className="text-xs text-[var(--ink-muted)]">{detail}</p></div></div>
}
function PipelineStage({ icon: Icon, name, role, runtime }: { icon: typeof Cpu; name: string; role: string; runtime: string }) {
  return <TableRow><TableCell className="pl-5"><span className="flex items-center gap-2.5 font-medium"><Icon className="size-4 text-[var(--ink-muted)]" aria-hidden="true" />{name}</span></TableCell><TableCell className="text-[var(--ink-muted)]">{role}</TableCell><TableCell className="text-[var(--ink-muted)]">{runtime}</TableCell><TableCell className="pr-5 text-right"><span className="pill positive">Ready</span></TableCell></TableRow>
}
function ModelRow({ layer, status, evidence }: { layer: string; status: string; evidence: string }) {
  return <TableRow><TableCell className="pl-5 font-medium">{layer}</TableCell><TableCell><span className="pill positive">{status}</span></TableCell><TableCell className="pr-5 text-[var(--ink-muted)]">{evidence}</TableCell></TableRow>
}
