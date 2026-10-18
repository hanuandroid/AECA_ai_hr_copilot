"use client";

// G-11: asks the signed-in administrator for their current password right before a high-impact change. The password
// lives only in this component's state until the submit click, is handed straight to the request (header
// X-Step-Up-Password, lib/api.ts) and is cleared on submit, cancel and close. Nothing is retried automatically.
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { StepUpAttempt } from "@/lib/step-up";

type Request = { title: string; description: string; run: (password: string) => Promise<StepUpAttempt> };

/** Page-level controller: `ask` opens the dialog for one operation; `dialog` is rendered once by the page. */
export function useStepUp() {
  const [request, setRequest] = useState<Request | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const ask = useCallback((title: string, description: string, run: Request["run"]) => {
    setError("");
    setRequest({ title, description, run });
  }, []);

  const cancel = useCallback(() => {
    setRequest(null);
    setError("");
  }, []);

  const submit = async (password: string) => {
    if (!request || busy) return;
    setBusy(true);
    setError("");
    try {
      const attempt = await request.run(password);
      if (attempt.kind === "retry") setError(attempt.message);
      else setRequest(null);
    } finally {
      setBusy(false);
    }
  };

  const dialog = request ? (
    <StepUpDialog title={request.title} description={request.description} busy={busy} error={error}
                  onSubmit={submit} onCancel={cancel} />
  ) : null;
  return { ask, dialog, open: request !== null };
}

export function StepUpDialog({
  title,
  description,
  busy,
  error,
  onSubmit,
  onCancel,
}: {
  title: string;
  description: string;
  busy: boolean;
  error: string;
  onSubmit: (password: string) => void;
  onCancel: () => void;
}) {
  const [password, setPassword] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus(); // closing the dialog unmounts it, which drops whatever was typed
  }, []);

  const submit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!password || busy) return;
    const typed = password;
    setPassword(""); // never kept after the attempt, whatever its outcome
    onSubmit(typed);
  };

  const cancel = () => {
    setPassword("");
    onCancel();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4">
      <div role="dialog" aria-modal="true" aria-labelledby="step-up-title" aria-describedby="step-up-description"
           className="w-full max-w-md rounded-xl border border-amber-300 bg-white p-5 shadow-xl"
           onKeyDown={(event) => { if (event.key === "Escape" && !busy) cancel(); }}>
        <h2 id="step-up-title" className="text-base font-semibold text-slate-900">{title}</h2>
        <p id="step-up-description" className="mt-1 text-sm text-slate-600">{description}</p>
        <form className="mt-4 space-y-3" onSubmit={submit}>
          <label className="block space-y-1 text-sm" htmlFor="step-up-password">
            <span>Your password</span>
            <Input id="step-up-password" ref={inputRef} type="password" autoComplete="current-password" value={password}
                   disabled={busy} onChange={(event) => setPassword(event.target.value)} />
          </label>
          {error ? <p role="alert" className="text-sm text-red-600">{error}</p> : null}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" disabled={busy} onClick={cancel}>Cancel</Button>
            <Button type="submit" disabled={busy || !password}>{busy ? "Confirming…" : "Confirm"}</Button>
          </div>
        </form>
      </div>
    </div>
  );
}
