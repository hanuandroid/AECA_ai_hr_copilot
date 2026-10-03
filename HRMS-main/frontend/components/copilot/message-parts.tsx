"use client";

import { AlertTriangle, CheckCircle2, ClipboardCheck, FileText, HelpCircle, Loader2, ShieldAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { ActionData, ChatPart, PolicySource, SqlData } from "@/lib/copilot-api";
import { isActionData, isPolicyData, isSqlData } from "@/lib/copilot-api";
import { friendlyError } from "@/lib/copilot-errors";
import { actionLabel, cellText, columnLabel, maskSensitive, visibleColumns } from "@/lib/copilot-display";

/** All AI text goes through this: React text nodes only (no HTML rendering), sensitive-looking values masked. */
export function SafeText({ text, className }: { text: string; className?: string }) {
  return <p className={className ?? "whitespace-pre-wrap break-words text-sm leading-relaxed text-slate-800"}>{maskSensitive(text)}</p>;
}

export function PolicySources({ sources }: { sources: PolicySource[] }) {
  if (!sources.length) return null;
  const unique = sources.filter(
    (source, index) => sources.findIndex((other) => other.title === source.title && other.section === source.section) === index
  );
  return (
    <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Sources</h4>
      <ul className="mt-2 space-y-1" aria-label="Policy sources">
        {unique.map((source) => (
          <li key={`${source.title}-${source.section}`} className="flex items-start gap-2 text-sm text-slate-700">
            <FileText className="mt-0.5 h-4 w-4 shrink-0 text-indigo-500" aria-hidden="true" />
            <span>
              <a href="/hr-policies" className="font-medium text-indigo-700 underline-offset-2 hover:underline">
                {maskSensitive(source.title)}
              </a>
              {source.section ? <span className="text-slate-500"> · {maskSensitive(source.section)}</span> : null}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

const MAX_ROWS_SHOWN = 50;

export function DataTable({ data }: { data: SqlData }) {
  const columns = visibleColumns(data.columns);
  if (!columns.length || !data.rows.length) {
    return data.row_count === 0 ? <p className="mt-2 text-sm text-slate-500">No matching records.</p> : null;
  }
  if (data.rows.length === 1 && columns.length === 1) {
    return (
      <div className="mt-3 inline-flex flex-col rounded-lg border border-indigo-100 bg-indigo-50 px-4 py-2">
        <span className="text-xs font-medium uppercase tracking-wide text-indigo-700">{columnLabel(columns[0])}</span>
        <span className="text-2xl font-semibold text-slate-900">{cellText(data.rows[0][columns[0]])}</span>
      </div>
    );
  }
  const rows = data.rows.slice(0, MAX_ROWS_SHOWN);
  return (
    <div className="mt-3 overflow-x-auto rounded-lg border border-slate-200">
      <table className="min-w-full text-left text-sm">
        <caption className="sr-only">Query results</caption>
        <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
          <tr>
            {columns.map((column) => (
              <th key={column} scope="col" className="px-3 py-2 font-semibold">{columnLabel(column)}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 bg-white">
          {rows.map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column} className="px-3 py-2 text-slate-700">{cellText(row[column])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {data.truncated || data.rows.length > MAX_ROWS_SHOWN ? (
        <p className="border-t border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-500">
          Showing the first {rows.length} rows. Ask a more specific question to narrow the results.
        </p>
      ) : null}
    </div>
  );
}

export type ActionState =
  | { state: "pending" }
  | { state: "confirming" }
  | { state: "cancelled" }
  | { state: "completed"; message: string }
  | { state: "failed"; message: string }
  | { state: "unknown"; message: string };

export function ActionCard({
  data,
  actionState,
  onConfirm,
  onCancel,
}: {
  data: ActionData;
  actionState: ActionState;
  onConfirm: (confirmationId: string) => void;
  onCancel: (confirmationId: string) => void;
}) {
  const confirmationId = data.confirmation_id;
  if (!confirmationId) return null;
  const busy = actionState.state === "confirming";
  const settled = actionState.state !== "pending" && !busy;
  const titleId = `action-${confirmationId.slice(0, 12)}`;
  return (
    <section aria-labelledby={titleId} className="mt-3 rounded-xl border-2 border-amber-300 bg-amber-50 p-4">
      <div className="flex items-center gap-2">
        <ClipboardCheck className="h-5 w-5 text-amber-700" aria-hidden="true" />
        <h4 id={titleId} className="text-sm font-semibold text-amber-900">{actionLabel(data.action)} — confirmation required</h4>
      </div>
      {data.summary ? <SafeText text={data.summary} className="mt-2 whitespace-pre-wrap text-sm text-slate-800" /> : null}
      <p className="mt-2 text-xs text-amber-800">Nothing has been changed yet. The action runs only if you confirm.</p>

      {actionState.state === "pending" || busy ? (
        <div className="mt-3 flex flex-wrap gap-2">
          <Button type="button" variant="outline" disabled={busy} onClick={() => onCancel(confirmationId)}>Cancel</Button>
          <Button type="button" disabled={busy} aria-disabled={busy} onClick={() => onConfirm(confirmationId)}>
            {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" /> : null}
            {busy ? "Confirming…" : "Confirm"}
          </Button>
        </div>
      ) : null}

      {settled ? (
        <div role="status" className="mt-3 flex items-start gap-2 text-sm">
          {actionState.state === "completed" ? <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" aria-hidden="true" /> : null}
          {actionState.state === "failed" || actionState.state === "unknown" ? (
            <AlertTriangle className="h-4 w-4 shrink-0 text-red-600" aria-hidden="true" />
          ) : null}
          <span className={actionState.state === "completed" ? "text-emerald-800" : actionState.state === "cancelled" ? "text-slate-600" : "text-red-700"}>
            {actionState.state === "completed" ? `Done: ${maskSensitive(actionState.message)}` : null}
            {actionState.state === "failed" ? `Not completed: ${maskSensitive(actionState.message)}` : null}
            {actionState.state === "unknown" ? actionState.message : null}
            {actionState.state === "cancelled" ? "Cancelled. Nothing was changed." : null}
          </span>
        </div>
      ) : null}
    </section>
  );
}

function Notice({ tone, label, text }: { tone: "clarify" | "refuse" | "error"; label: string; text: string }) {
  const styles = {
    clarify: "border-sky-200 bg-sky-50 text-sky-900",
    refuse: "border-slate-300 bg-slate-100 text-slate-800",
    error: "border-red-200 bg-red-50 text-red-800",
  }[tone];
  const Icon = tone === "clarify" ? HelpCircle : tone === "refuse" ? ShieldAlert : AlertTriangle;
  return (
    <div className={`rounded-lg border p-3 ${styles}`}>
      <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide">
        <Icon className="h-4 w-4" aria-hidden="true" />
        {label}
      </p>
      <SafeText text={text} className="mt-1 whitespace-pre-wrap text-sm" />
    </div>
  );
}

export function PartView({
  part,
  actionStates,
  onConfirm,
  onCancel,
}: {
  part: ChatPart;
  actionStates: Record<string, ActionState>;
  onConfirm: (confirmationId: string) => void;
  onCancel: (confirmationId: string) => void;
}) {
  if (part.status === "failed") {
    return <Notice tone="error" label="Couldn't answer this part" text={friendlyError(part.error_code ? { code: part.error_code, message: "" } : undefined, 500)} />;
  }
  if (part.route === "REFUSAL") return <Notice tone="refuse" label="Can't help with that" text={part.message} />;
  if (part.route === "CLARIFICATION" || part.status === "clarification_required") {
    return <Notice tone="clarify" label="Needs clarification" text={part.message} />;
  }
  if (part.route === "ACTION" && isActionData(part.data) && part.data.confirmation_id) {
    const state = actionStates[part.data.confirmation_id] ?? { state: "pending" };
    return <ActionCard data={part.data} actionState={state} onConfirm={onConfirm} onCancel={onCancel} />;
  }
  return (
    <div>
      <SafeText text={part.message} />
      {part.route === "POLICY" && isPolicyData(part.data) ? <PolicySources sources={part.data.sources} /> : null}
      {part.route === "SQL" && isSqlData(part.data) ? <DataTable data={part.data} /> : null}
    </div>
  );
}
