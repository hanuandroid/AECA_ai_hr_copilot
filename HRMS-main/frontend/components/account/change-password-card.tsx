"use client";

// B-17 follow-up: self-service password change on /me (POST /api/v1/auth/change-password). The backend owns the
// password policy (8-128 characters, not blank, different from the current one) and the throttle; this form mirrors the
// length rule only to avoid a wasted request. A successful change signs out every session, including this one, so the
// user is sent to /login. Passwords live only in this component's state until the request is sent and are cleared after
// every attempt; they are never logged or placed in a URL.
import { useState } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { apiErrorCode, changePassword } from "@/lib/api";
import { endSession, getToken, isAuthFailure } from "@/lib/session";

const MIN_LENGTH = 8;
const MAX_LENGTH = 128;

const ERROR_MESSAGES: Record<string, string> = {
  INVALID_CURRENT_PASSWORD: "The current password is incorrect.",
  PASSWORD_UNCHANGED: "The new password must be different from the current one.",
  INVALID_NEW_PASSWORD: "The new password must not be blank.",
  TOO_MANY_ATTEMPTS: "Too many attempts. Wait a minute and try again.",
};
const LENGTH_MESSAGE = `The new password must be ${MIN_LENGTH} to ${MAX_LENGTH} characters.`;
const GENERIC_MESSAGE = "Your password could not be changed. Please try again.";
const NETWORK_MESSAGE = "Unable to reach the server. Your password was not changed.";

export function messageForFailure(status: number, code: string | null): string {
  if (status === 422) return LENGTH_MESSAGE;
  if (status === 429) return ERROR_MESSAGES.TOO_MANY_ATTEMPTS;
  return (code && ERROR_MESSAGES[code]) || GENERIC_MESSAGE;
}

export function ChangePasswordCard() {
  const router = useRouter();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const clearFields = () => {
    setCurrent("");
    setNext("");
    setConfirm("");
  };

  const onSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    if (next !== confirm) {
      setError("The new passwords do not match.");
      setNext("");
      setConfirm("");
      return;
    }
    if (next.length < MIN_LENGTH || next.length > MAX_LENGTH) {
      setError(LENGTH_MESSAGE);
      setNext("");
      setConfirm("");
      return;
    }
    const token = getToken();
    if (!token) return endSession(router);
    const payload = { current_password: current, new_password: next };
    clearFields();
    setSubmitting(true);
    try {
      const result = await changePassword(token, payload);
      if (result.ok) {
        endSession(router, "password-changed");
        return;
      }
      if (isAuthFailure(result.status)) {
        endSession(router);
        return;
      }
      setError(messageForFailure(result.status, apiErrorCode(result.body)));
    } catch {
      setError(NETWORK_MESSAGE);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Change password</CardTitle>
      </CardHeader>
      <CardContent>
        <form className="space-y-3" onSubmit={onSubmit} aria-describedby="change-password-help">
          <p id="change-password-help" className="text-xs text-muted-foreground">
            Changing your password signs you out on all your devices, including this one.
          </p>
          <label className="block space-y-1 text-sm" htmlFor="current-password">
            <span>Current password</span>
            <Input id="current-password" type="password" autoComplete="current-password" required value={current}
                   onChange={(e) => setCurrent(e.target.value)} />
          </label>
          <label className="block space-y-1 text-sm" htmlFor="new-password">
            <span>New password</span>
            <Input id="new-password" type="password" autoComplete="new-password" required minLength={MIN_LENGTH}
                   maxLength={MAX_LENGTH} value={next} onChange={(e) => setNext(e.target.value)} />
          </label>
          <label className="block space-y-1 text-sm" htmlFor="confirm-password">
            <span>Confirm new password</span>
            <Input id="confirm-password" type="password" autoComplete="new-password" required value={confirm}
                   onChange={(e) => setConfirm(e.target.value)} />
          </label>
          {error ? <p role="alert" className="text-sm text-red-600">{error}</p> : null}
          <Button type="submit" disabled={submitting}>
            {submitting ? "Changing..." : "Change password"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
