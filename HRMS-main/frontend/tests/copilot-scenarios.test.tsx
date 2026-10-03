/**
 * The eight Phase 9 end-to-end scenarios, run as full-flow integration tests (the repository has no E2E
 * framework, and Phase 9 must not add one). A stateful fake backend mimics the server contract:
 * owner-scoped conversations, persisted history, follow-ups, single-use confirmations and business state.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CopilotShell } from "@/components/copilot/copilot-shell";
import type { ChatPart, ConversationMessage } from "@/lib/copilot-api";

import { actionPart, chatResponse, fail, mockBackend, ok, policyPart, sqlPart } from "./helpers";

type Stored = { id: string; owner: string; title: string; messages: ConversationMessage[] };

function fakeServer(currentUser: { name: string; role: "EMPLOYEE" | "MANAGER" }) {
  const conversations = new Map<string, Stored>();
  const tokens = new Map<string, "PENDING" | "USED">();
  const leaves = { rahul: "PENDING" };
  let counter = 0;

  const answer = (text: string, history: ConversationMessage[]): ChatPart[] => {
    const previous = [...history].reverse().find((m) => m.role === "USER")?.content ?? "";
    if (/sick leave policy/i.test(text)) return [policyPart("Employees get 10 sick leave days per year.")];
    if (/wfh policy/i.test(text)) return [policyPart("Employees may work from home two days a week.")];
    if (/apply to managers/i.test(text) && /wfh/i.test(previous)) return [policyPart("Yes, the WFH policy applies to managers too.")];
    if (/leave balance|^balance\.?$/i.test(text)) {
      return [sqlPart([{ leave_type: "CASUAL", remaining: 10 }, { leave_type: "SICK", remaining: 9 }], ["leave_type", "remaining"], "Here is your leave balance.")];
    }
    if (/tell me about leave/i.test(text)) {
      return [{ route: "CLARIFICATION", status: "clarification_required", message: "Do you mean the leave policy or your leave balance?" }];
    }
    if (/approve rahul/i.test(text)) {
      if (currentUser.role !== "MANAGER") throw Object.assign(new Error("denied"), { reply: fail(403, "AI_PERMISSION_DENIED") });
      const token = `tok-${++counter}`;
      tokens.set(token, "PENDING");
      return [actionPart(token, "Approve leave request #20 for Rahul Mehta")];
    }
    if (/xss/i.test(text)) return [policyPart('<img src=x onerror="alert(1)"><script>alert(1)</script>')];
    return [{ route: "CLARIFICATION", status: "clarification_required", message: "Could you tell me more?" }];
  };

  const server = mockBackend({
    "GET /api/v1/chat/conversations": (call) => {
      const id = call.path.split("?")[0].split("/")[5];
      if (!id) {
        return ok([...conversations.values()].filter((c) => c.owner === currentUser.name).reverse().map((c) => ({
          conversation_id: c.id, title: c.title, message_count: c.messages.length,
          created_at: "2026-10-03T09:00:00", updated_at: new Date().toISOString().replace("Z", "") })));
      }
      const stored = conversations.get(id);
      if (!stored || stored.owner !== currentUser.name) return fail(404, "AI_CONVERSATION_NOT_FOUND");
      return ok({ conversation_id: id, title: stored.title, message_count: stored.messages.length, created_at: "", updated_at: "",
                  messages: stored.messages });
    },
    "POST /api/v1/chat": (call) => {
      const body = call.body as { message: string; conversation_id?: string };
      let stored = body.conversation_id ? conversations.get(body.conversation_id) : undefined;
      if (body.conversation_id && (!stored || stored.owner !== currentUser.name)) return fail(404, "AI_CONVERSATION_NOT_FOUND");
      if (!stored) {
        stored = { id: `00000000-0000-4000-8000-00000000000${++counter}`, owner: currentUser.name, title: body.message, messages: [] };
        conversations.set(stored.id, stored);
      }
      const history = [...stored.messages];
      const seq = stored.messages.length;
      stored.messages.push({ sequence: seq + 1, role: "USER", content: body.message, created_at: "" });
      try {
        const parts = answer(body.message, history);
        const response = chatResponse(parts, stored.id, { follow_up: history.length > 0 });
        stored.messages.push({ sequence: seq + 2, role: "ASSISTANT", content: response.message, route: response.routes.join("+"),
                               status: response.status, created_at: "" });
        return ok(response);
      } catch (error) {
        const reply = (error as { reply: ReturnType<typeof fail> }).reply;
        stored.messages.push({ sequence: seq + 2, role: "ASSISTANT", content: "denied", status: "error",
                               error_code: "AI_PERMISSION_DENIED", created_at: "" });
        return { ...reply, headers: { ...reply.headers, "X-Conversation-Id": stored.id } };
      }
    },
    "POST /api/v1/chat/actions/confirm": (call) => {
      const token = String(call.body?.confirmation_id);
      if (tokens.get(token) !== "PENDING") return fail(400, "AI_CONFIRMATION_INVALID");
      tokens.set(token, "USED");
      leaves.rahul = "APPROVED";
      return ok({ status: "completed", message: "Leave approved." });
    },
  });
  return { ...server, conversations, leaves };
}

let pushedPath = "/copilot";

function mount(initialConversationId: string | null = null, role: "EMPLOYEE" | "MANAGER" = "EMPLOYEE") {
  const onConversationChange = vi.fn((id: string | null) => { pushedPath = id ? `/copilot/${id}` : "/copilot"; });
  const utils = render(
    <CopilotShell token="t" roleHint={role} initialConversationId={initialConversationId} onUnauthorized={vi.fn()}
                  onConversationChange={onConversationChange} />
  );
  return { ...utils, onConversationChange };
}

async function ask(user: ReturnType<typeof userEvent.setup>, text: string) {
  await user.type(screen.getByLabelText("Ask PeopleOps Copilot"), `${text}{Enter}`);
}

beforeEach(() => {
  pushedPath = "/copilot";
});

describe("E2E scenarios", () => {
  it("1 - policy question with citations", async () => {
    fakeServer({ name: "employee", role: "EMPLOYEE" });
    const user = userEvent.setup();
    mount();
    await ask(user, "What is the sick leave policy?");
    expect(await screen.findByText("Employees get 10 sick leave days per year.")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Policy sources" })).toHaveTextContent("Leave Policy");
  });

  it("2 - follow-up stays in the same conversation", async () => {
    const server = fakeServer({ name: "employee", role: "EMPLOYEE" });
    const user = userEvent.setup();
    mount();
    await ask(user, "What is the WFH policy?");
    await screen.findByText("Employees may work from home two days a week.");
    await ask(user, "Does that apply to managers?");
    expect(await screen.findByText("Yes, the WFH policy applies to managers too.")).toBeInTheDocument();
    expect(server.conversations.size).toBe(1);
  });

  it("3 - data question gives a structured answer", async () => {
    fakeServer({ name: "employee", role: "EMPLOYEE" });
    const user = userEvent.setup();
    mount();
    await ask(user, "Show my leave balance.");
    const table = await screen.findByRole("table");
    expect(within(table).getByText("SICK")).toBeInTheDocument();
    expect(within(table).getByText("9")).toBeInTheDocument();
  });

  it("4 - action is proposed, nothing executes until Confirm, then the backend result is shown", async () => {
    const server = fakeServer({ name: "manager", role: "MANAGER" });
    const user = userEvent.setup();
    mount(null, "MANAGER");
    await ask(user, "Approve Rahul's leave.");
    const card = await screen.findByRole("region", { name: /Leave approval/ });
    expect(server.leaves.rahul).toBe("PENDING");
    await user.click(within(card).getByRole("button", { name: "Confirm" }));
    expect(await within(card).findByText("Done: Leave approved.")).toBeInTheDocument();
    expect(server.leaves.rahul).toBe("APPROVED");
    expect(server.calls.filter((c) => c.path === "/api/v1/chat/actions/confirm")).toHaveLength(1);
  });

  it("5 - clarification then answer", async () => {
    fakeServer({ name: "employee", role: "EMPLOYEE" });
    const user = userEvent.setup();
    mount();
    await ask(user, "Tell me about leave.");
    expect(await screen.findByText("Do you mean the leave policy or your leave balance?")).toBeInTheDocument();
    expect(screen.getByText("Needs clarification")).toBeInTheDocument();
    await ask(user, "Balance.");
    expect(await screen.findByRole("table")).toBeInTheDocument();
  });

  it("6 - refresh restores messages from the backend and follow-up still works", async () => {
    const server = fakeServer({ name: "employee", role: "EMPLOYEE" });
    const user = userEvent.setup();
    const first = mount();
    await ask(user, "What is the WFH policy?");
    await screen.findByText("Employees may work from home two days a week.");
    expect(pushedPath).toMatch(/^\/copilot\/00000000-/);
    first.unmount(); // "refresh": all browser state is gone except the URL

    mount(decodeURIComponent(pushedPath.split("/")[2]));
    expect(await screen.findByText("Employees may work from home two days a week.")).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "Conversation messages" })).getByText("What is the WFH policy?")).toBeInTheDocument();
    await ask(user, "Does that apply to managers?");
    expect(await screen.findByText("Yes, the WFH policy applies to managers too.")).toBeInTheDocument();
    expect(server.conversations.size).toBe(1);
  });

  it("7 - an employee's manager-only action is rejected by the backend and shown safely", async () => {
    const server = fakeServer({ name: "employee", role: "EMPLOYEE" });
    const user = userEvent.setup();
    mount();
    await ask(user, "Approve Rahul's leave.");
    expect(await screen.findByRole("alert")).toHaveTextContent("You don't have permission to do that.");
    expect(screen.queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument();
    expect(server.leaves.rahul).toBe("PENDING");
    expect(pushedPath).toMatch(/^\/copilot\/00000000-/); // the failed turn's conversation is still usable
  });

  it("8 - malicious HTML from the backend renders as text", async () => {
    fakeServer({ name: "employee", role: "EMPLOYEE" });
    const alert = vi.spyOn(window, "alert").mockImplementation(() => undefined);
    const user = userEvent.setup();
    mount();
    await ask(user, "xss please");
    expect(await screen.findByText('<img src=x onerror="alert(1)"><script>alert(1)</script>')).toBeInTheDocument();
    expect(document.querySelectorAll("img, script")).toHaveLength(0);
    expect(alert).not.toHaveBeenCalled();
  });

  it("cross-user: another user's conversation id is denied generically", async () => {
    const server = fakeServer({ name: "employee", role: "EMPLOYEE" });
    server.conversations.set("someone-else", { id: "someone-else", owner: "manager", title: "Private", messages: [] });
    mount("someone-else");
    expect(await screen.findByRole("alert")).toHaveTextContent("Conversation unavailable.");
    await waitFor(() => expect(screen.queryByText("Private")).not.toBeInTheDocument());
  });
});
