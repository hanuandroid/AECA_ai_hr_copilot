"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { UsageDashboard } from "@/components/copilot/usage-dashboard";
import { Sidebar } from "@/components/layout/sidebar";
import { Topbar } from "@/components/layout/topbar";
import { fetchProfile } from "@/lib/api";
import { endSession } from "@/lib/session";

/** Admin AI usage dashboard. Access is decided by the backend (AUDIT_READ_ORG); a 403 is shown as such. */
export default function AIUsagePage() {
  const router = useRouter();
  const [token, setToken] = useState<string | null>(null);
  const [name, setName] = useState("User");

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
  }, [clearAuthAndRedirect]);

  return (
    <main className="flex min-h-screen">
      <Sidebar />
      <section className="flex w-full min-w-0 flex-col">
        <Topbar name={name} title="AI Usage" />
        <div className="space-y-3 p-4 md:p-6">
          <div className="flex justify-end">
            <Link href="/admin/ai-audit" className="text-sm font-medium text-indigo-700 hover:underline">AI audit log</Link>
          </div>
          {token ? <UsageDashboard token={token} onUnauthorized={clearAuthAndRedirect} /> : null}
        </div>
      </section>
    </main>
  );
}
