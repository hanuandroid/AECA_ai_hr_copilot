// RBAC step 6 (decision 5): who sees an announcement or poll. UX only - the backend decides the audience and the team
// (a manager asking for ORG gets 403; the team is always the creator's own) and filters every list it returns.
import { Badge } from "@/components/ui/badge";
import type { ContentAudience } from "@/lib/api";

/** Audience the form sends: admins choose; managers always post to their team. */
export function audienceFor(role: string, choice: ContentAudience): ContentAudience {
  return role === "ADMIN" ? choice : "TEAM";
}

export function AudienceField({
  role,
  value,
  onChange,
}: {
  role: string;
  value: ContentAudience;
  onChange: (value: ContentAudience) => void;
}) {
  if (role !== "ADMIN") {
    return <p className="text-xs text-muted-foreground">Visible to you and your direct reports.</p>;
  }
  return (
    <label className="flex items-center gap-2 text-sm">
      <span className="text-muted-foreground">Audience</span>
      <select
        aria-label="Audience"
        className="h-9 rounded-md border border-border bg-white px-3 text-sm"
        value={value}
        onChange={(e) => onChange(e.target.value as ContentAudience)}
      >
        <option value="ORG">Everyone</option>
        <option value="TEAM">My team (direct reports)</option>
      </select>
    </label>
  );
}

export function AudienceBadge({ audience }: { audience: ContentAudience | undefined }) {
  if (audience !== "TEAM") return null;
  return <Badge className="bg-indigo-100 text-indigo-700">Team</Badge>;
}

export function audienceError(status: number, fallback: string): string {
  return status === 403 ? "You can only post to your own team." : fallback;
}
