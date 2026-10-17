"use client";

import { BarChart3, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { CopilotShell } from "@/components/copilot/copilot-shell";
import { Sidebar } from "@/components/layout/sidebar";
import { Topbar } from "@/components/layout/topbar";
import { fetchProfile } from "@/lib/api";
import { fetchAiPermissions } from "@/lib/copilot-api";
import { endSession } from "@/lib/session";

/**
 * /copilot              -> new or most recent conversation
 * /copilot/<id>         -> that conversation, loaded from the backend (its ownership check is authoritative)
 * One optional catch-all route, so switching conversations updates the URL without remounting the chat.
 */
export default function CopilotPage() {
  const router = useRouter();
  const params = useParams<{ conversation?: string[] }>();
  const initialConversationId = useMemo(() => {
    const segments = params?.conversation ?? [];
    return segments.length === 1 ? decodeURIComponent(segments[0]) : null;
  }, [params]);

  const [token, setToken] = useState<string | null>(null);
  const [name, setName] = useState("User");
  const [roleHint, setRoleHint] = useState<string | null>(null);
  const [capabilities, setCapabilities] = useState<string[] | null>(null);

  const clearAuthAndRedirect = useCallback(() => {
    endSession(router);
  }, [router]);

  useEffect(() => {
    const stored = localStorage.getItem("hrms_access_token");
    if (!stored) return clearAuthAndRedirect();
    setToken(stored);
    fetchProfile(stored)
      .then((result) => {
        if (result.status === 401) return clearAuthAndRedirect();
        if (result.ok && "success" in result.body && result.body.success) setName(result.body.data.name);
      })
      .catch(() => undefined);
    // Role-specific mode: role and capabilities as derived by the backend. UX hint only (prompts, links).
    fetchAiPermissions(stored)
      .then((result) => {
        if (result.status === 401) return clearAuthAndRedirect();
        if (result.ok && result.data) {
          setRoleHint(result.data.role);
          setCapabilities(result.data.capabilities);
        }
      })
      .catch(() => undefined);
  }, [clearAuthAndRedirect]);

  const onConversationChange = useCallback((conversationId: string | null) => {
    const path = conversationId ? `/copilot/${encodeURIComponent(conversationId)}` : "/copilot";
    if (window.location.pathname !== path) window.history.pushState(null, "", path);
  }, []);

  return (
    <main className="flex min-h-screen">
      <Sidebar />
      <section className="flex w-full min-w-0 flex-col">
        <Topbar name={name} title="AI Copilot" />
        <div className="space-y-2 p-3 md:p-4">
          {capabilities?.includes("AUDIT_READ_ORG") ? (
            <div className="flex justify-end gap-4">
              <Link href="/admin/ai-usage" className="inline-flex items-center gap-1 text-sm font-medium text-indigo-700 hover:underline">
                <BarChart3 className="h-4 w-4" aria-hidden="true" />
                AI usage
              </Link>
              <Link href="/admin/ai-audit" className="inline-flex items-center gap-1 text-sm font-medium text-indigo-700 hover:underline">
                <ShieldCheck className="h-4 w-4" aria-hidden="true" />
                AI audit log
              </Link>
            </div>
          ) : null}
          {token ? (
            <CopilotShell
              token={token}
              roleHint={roleHint}
              capabilities={capabilities}
              initialConversationId={initialConversationId}
              onUnauthorized={clearAuthAndRedirect}
              onConversationChange={onConversationChange}
            />
          ) : null}
        </div>
      </section>
    </main>
  );
}
