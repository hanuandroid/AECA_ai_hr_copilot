// RBAC remediation step 9: the ONE frontend page-access map. UX only - the backend is the authorization boundary and
// refuses every API call these pages make for a role that is not listed here (tests/rbac/test_route_permissions.py).
// Each row mirrors the backend rule of the page's primary API; tests/permissions.test.ts pins that every page under
// app/ has a row. The role comes from the server profile (GET /employees/me), never from the hrms_role cookie.

export type AppRole = "ADMIN" | "MANAGER" | "EMPLOYEE";

const EVERYONE: readonly AppRole[] = ["ADMIN", "MANAGER", "EMPLOYEE"];
const MANAGERS: readonly AppRole[] = ["ADMIN", "MANAGER"];
const ADMINS: readonly AppRole[] = ["ADMIN"];

/** Paths reachable without signing in; never guarded. */
export const PUBLIC_PATHS: readonly string[] = ["/", "/login"];

export const PAGE_ACCESS: readonly { path: string; roles: readonly AppRole[] }[] = [
  { path: "/dashboard", roles: EVERYONE },
  { path: "/copilot", roles: EVERYONE },
  { path: "/me", roles: EVERYONE },
  { path: "/me/documents", roles: EVERYONE },
  { path: "/finance", roles: EVERYONE },
  { path: "/attendance", roles: EVERYONE },
  { path: "/leaves", roles: EVERYONE },
  { path: "/announcements", roles: EVERYONE },
  { path: "/hr-policies", roles: EVERYONE },
  { path: "/polls", roles: EVERYONE },
  { path: "/team-calendar", roles: EVERYONE },
  { path: "/tickets", roles: EVERYONE },
  { path: "/organization", roles: EVERYONE }, // limited org chart (decision 1)
  { path: "/employees", roles: MANAGERS }, // directory: manager self + reports, admin all (decision 1)
  { path: "/employees/projects", roles: MANAGERS },
  { path: "/employees/payslips", roles: MANAGERS },
  { path: "/employees/new", roles: ADMINS }, // create / edit employees
  { path: "/admin", roles: ADMINS }, // /admin/ai-audit, /admin/ai-usage (organisation-wide AI audit and metrics)
];

function matches(pathname: string, path: string): boolean {
  return pathname === path || pathname.startsWith(`${path}/`);
}

/** Roles allowed on `pathname` (most specific row wins); "public" for PUBLIC_PATHS; null when no page is mapped. */
export function requiredRoles(pathname: string): readonly AppRole[] | "public" | null {
  if (PUBLIC_PATHS.includes(pathname)) return "public";
  let best: { path: string; roles: readonly AppRole[] } | null = null;
  for (const row of PAGE_ACCESS) {
    if (matches(pathname, row.path) && (!best || row.path.length > best.path.length)) best = row;
  }
  return best ? best.roles : null;
}

/** Fails closed: unknown or missing roles and unmapped paths are never allowed. */
export function canAccess(role: string | null | undefined, pathname: string): boolean {
  const roles = requiredRoles(pathname);
  if (roles === "public") return true;
  if (roles === null || !role) return false;
  return (roles as readonly string[]).includes(role);
}

/** True when every role may open the page, so no profile check is needed before rendering it. */
export function isOpenToEveryRole(pathname: string): boolean {
  const roles = requiredRoles(pathname);
  return roles === "public" || (roles !== null && EVERYONE.every((role) => roles.includes(role)));
}
