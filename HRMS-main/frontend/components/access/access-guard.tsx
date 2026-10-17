"use client";

// RBAC remediation step 9: direct-URL guard for every page (mounted once in app/layout.tsx). UX only - the backend
// refuses the page's API calls anyway. The role comes from the server profile (GET /employees/me) for the current token,
// never from the hrms_role cookie or local storage. Restricted pages render nothing until the role is known, then the
// page or the access-denied view; if the role cannot be established the page is not shown (fail closed). A 401 is left
// to the page, whose existing logic clears the session and redirects to /login.
import { createContext, useContext, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { fetchProfile } from "@/lib/api";
import { canAccess, isOpenToEveryRole, requiredRoles } from "@/lib/permissions";

type ViewerState = { token: string | null; role: string | null; status: "idle" | "loading" | "ready" | "unauthorized" | "failed" };

const ViewerRoleContext = createContext<string | null>(null);

/** The signed-in user's role from the server profile, or null while unknown (navigation then shows only shared pages). */
export function useViewerRole(): string | null {
  return useContext(ViewerRoleContext);
}

function readToken(): string | null {
  try {
    return typeof window === "undefined" ? null : localStorage.getItem("hrms_access_token");
  } catch {
    return null;
  }
}

export function AccessDenied() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50 p-6">
      <section role="alert" className="max-w-md rounded-xl border border-border bg-white p-6 text-center shadow-sm">
        <h1 className="text-lg font-semibold text-slate-900">Access denied</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          You don&apos;t have permission to view this page. If you think you should, ask an administrator.
        </p>
        <Link href="/dashboard" className="mt-4 inline-block text-sm font-medium text-indigo-700 hover:underline">
          Go to the dashboard
        </Link>
      </section>
    </main>
  );
}

export function AccessGuard({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() || "/";
  const [viewer, setViewer] = useState<ViewerState>({ token: null, role: null, status: "idle" });
  const isPublic = requiredRoles(pathname) === "public";

  useEffect(() => {
    if (isPublic) return;
    const token = readToken();
    if (!token) {
      setViewer({ token: null, role: null, status: "unauthorized" });
      return;
    }
    if (viewer.token === token && viewer.status !== "idle") return; // already resolved (or resolving) for this session
    let cancelled = false;
    setViewer({ token, role: null, status: "loading" });
    fetchProfile(token)
      .then((result) => {
        if (cancelled) return;
        if (result.status === 401) return setViewer({ token, role: null, status: "unauthorized" });
        const role = result.ok && "success" in result.body && result.body.success ? result.body.data.role : null;
        setViewer({ token, role, status: role ? "ready" : "failed" });
      })
      .catch(() => {
        if (!cancelled) setViewer({ token, role: null, status: "failed" });
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname, isPublic]);

  const role = viewer.status === "ready" ? viewer.role : null;
  let content: React.ReactNode = children;
  if (!isPublic && !isOpenToEveryRole(pathname) && requiredRoles(pathname) !== null) {
    if (viewer.status === "unauthorized") content = children; // the page redirects to /login
    else if (viewer.status !== "ready" && viewer.status !== "failed") content = null; // waiting: show nothing restricted
    else content = canAccess(role, pathname) ? children : <AccessDenied />;
  }
  return <ViewerRoleContext.Provider value={role}>{content}</ViewerRoleContext.Provider>;
}
