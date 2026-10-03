"use client";

import { Archive, Loader2, MessageSquarePlus, PanelLeft, Send, Sparkles, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { ActionState, PartView, SafeText } from "@/components/copilot/message-parts";
import { Button } from "@/components/ui/button";
import {
  ChatPart,
  Conversation,
  ConversationMessage,
  archiveConversation,
  confirmAction,
  getConversation,
  listConversations,
  sendChat,
} from "@/lib/copilot-api";
import { conversationGroup, copilotMode, maskSensitive } from "@/lib/copilot-display";
import { errorReference, friendlyError } from "@/lib/copilot-errors";

const MAX_MESSAGE = 1000;

export type UiMessage =
  | { id: string; kind: "user"; text: string }
  | { id: string; kind: "assistant"; parts: ChatPart[] }
  | { id: string; kind: "history"; message: ConversationMessage }
  | { id: string; kind: "error"; text: string; reference?: string; retryText?: string };

export type CopilotShellProps = {
  token: string;
  /** UI hint only (example prompts). Never used to allow or deny anything. */
  roleHint: string | null;
  /** Capabilities reported by GET /chat/permissions (backend-derived). UI hint only: filters example prompts. */
  capabilities?: string[] | null;
  initialConversationId: string | null;
  onUnauthorized: () => void;
  onConversationChange: (conversationId: string | null) => void;
};

let messageCounter = 0;
const nextId = () => `m${++messageCounter}`;

function historyToUi(messages: ConversationMessage[]): UiMessage[] {
  return messages.map((message) =>
    message.role === "USER" ? { id: nextId(), kind: "user", text: message.content } : { id: nextId(), kind: "history", message }
  );
}

export function CopilotShell({ token, roleHint, capabilities, initialConversationId, onUnauthorized, onConversationChange }: CopilotShellProps) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [listState, setListState] = useState<"loading" | "ready" | "error">("loading");
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [loadingConversation, setLoadingConversation] = useState(false);
  const [banner, setBanner] = useState("");
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [actionStates, setActionStates] = useState<Record<string, ActionState>>({});
  const [listOpen, setListOpen] = useState(false);
  const [archiveCandidate, setArchiveCandidate] = useState<string | null>(null);

  const sendingRef = useRef(false); // synchronous guard: a double click cannot send twice
  const confirmingRef = useRef<Set<string>>(new Set()); // one confirmation request per token, ever
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);

  const refreshList = useCallback(async () => {
    const result = await listConversations(token);
    if (result.status === 401) return onUnauthorized();
    if (result.ok && result.data) {
      setConversations(result.data);
      setListState("ready");
    } else {
      setListState("error");
    }
  }, [token, onUnauthorized]);

  const openConversation = useCallback(
    async (conversationId: string) => {
      setBanner("");
      setLoadingConversation(true);
      setActionStates({});
      setMessages([]);
      const result = await getConversation(token, conversationId);
      setLoadingConversation(false);
      if (result.status === 401) return onUnauthorized();
      if (!result.ok || !result.data) {
        // Generic on purpose: someone else's conversation looks exactly like a missing one.
        setActiveId(null);
        onConversationChange(null);
        setBanner(friendlyError(result.error, result.status));
        return;
      }
      setActiveId(result.data.conversation_id);
      setMessages(historyToUi(result.data.messages));
      setListOpen(false);
    },
    [token, onUnauthorized, onConversationChange]
  );

  useEffect(() => {
    void refreshList();
    if (initialConversationId) void openConversation(initialConversationId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "end" });
  }, [messages, sending]);

  const startNewConversation = () => {
    setActiveId(null);
    setMessages([]);
    setActionStates({});
    setInput("");
    setBanner("");
    setListOpen(false);
    onConversationChange(null);
    inputRef.current?.focus();
  };

  const selectConversation = (conversationId: string) => {
    if (conversationId === activeId || sendingRef.current) return;
    onConversationChange(conversationId);
    void openConversation(conversationId);
  };

  const send = async (raw: string) => {
    const text = raw.trim();
    if (!text || text.length > MAX_MESSAGE || sendingRef.current) return;
    sendingRef.current = true;
    setSending(true);
    setBanner("");
    setInput("");
    setMessages((current) => [...current, { id: nextId(), kind: "user", text }]);
    try {
      const result = await sendChat(token, text, activeId);
      if (result.status === 401) return onUnauthorized();
      if (result.ok && result.data) {
        const response = result.data;
        if (response.conversation_id !== activeId) {
          setActiveId(response.conversation_id);
          onConversationChange(response.conversation_id);
        }
        setMessages((current) => [...current, { id: nextId(), kind: "assistant", parts: response.parts }]);
      } else {
        // A failed first turn still created a conversation: adopt its id (from the backend header only).
        if (!activeId && result.conversationId) {
          setActiveId(result.conversationId);
          onConversationChange(result.conversationId);
        }
        const notFound = result.error?.code === "AI_CONVERSATION_NOT_FOUND";
        if (notFound) {
          setActiveId(null);
          onConversationChange(null);
        }
        setMessages((current) => [
          ...current,
          {
            id: nextId(),
            kind: "error",
            text: friendlyError(result.error, result.status),
            reference: errorReference(result.requestId),
            retryText: notFound ? undefined : text,
          },
        ]);
      }
      void refreshList();
    } finally {
      sendingRef.current = false;
      setSending(false);
      inputRef.current?.focus();
    }
  };

  const confirm = async (confirmationId: string) => {
    if (confirmingRef.current.has(confirmationId)) return; // never send the same confirmation twice
    confirmingRef.current.add(confirmationId);
    setActionStates((current) => ({ ...current, [confirmationId]: { state: "confirming" } }));
    const result = await confirmAction(token, confirmationId);
    if (result.status === 401) return onUnauthorized();
    let next: ActionState;
    if (result.ok && result.data) {
      next = result.data.status === "completed"
        ? { state: "completed", message: result.data.message }
        : { state: "failed", message: result.data.message || "The HR system rejected this request." };
    } else if (result.status === 0) {
      // No response: the outcome is unknown. Do not retry automatically.
      next = {
        state: "unknown",
        message: "We couldn't confirm whether this action completed. Please check the related page before asking again.",
      };
    } else {
      const reference = errorReference(result.requestId);
      next = { state: "failed", message: `${friendlyError(result.error, result.status)}${reference ? ` (Reference: ${reference})` : ""}` };
    }
    setActionStates((current) => ({ ...current, [confirmationId]: next }));
  };

  const cancel = (confirmationId: string) => {
    confirmingRef.current.add(confirmationId); // a cancelled proposal can no longer be confirmed from this UI
    setActionStates((current) => ({ ...current, [confirmationId]: { state: "cancelled" } }));
  };

  const archive = async (conversationId: string) => {
    setArchiveCandidate(null);
    const result = await archiveConversation(token, conversationId);
    if (result.status === 401) return onUnauthorized();
    if (!result.ok) {
      setBanner(friendlyError(result.error, result.status));
      return;
    }
    if (conversationId === activeId) startNewConversation();
    void refreshList();
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void send(input);
    }
  };

  const groups = (["Today", "Yesterday", "Earlier"] as const)
    .map((label) => ({ label, items: conversations.filter((item) => conversationGroup(item.updated_at) === label) }))
    .filter((group) => group.items.length > 0);

  const conversationList = (
    <nav aria-label="Conversations" className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-2 p-3">
        <Button type="button" onClick={startNewConversation} className="w-full gap-2">
          <MessageSquarePlus className="h-4 w-4" aria-hidden="true" />
          New conversation
        </Button>
        <Button type="button" variant="outline" size="sm" className="lg:hidden" onClick={() => setListOpen(false)} aria-label="Close conversations">
          <X className="h-4 w-4" aria-hidden="true" />
        </Button>
      </div>
      <div className="flex-1 overflow-y-auto px-2 pb-3">
        {listState === "loading" ? <p className="px-2 text-sm text-slate-500">Loading conversations…</p> : null}
        {listState === "error" ? <p className="px-2 text-sm text-red-600" role="alert">Couldn't load conversations.</p> : null}
        {listState === "ready" && conversations.length === 0 ? <p className="px-2 text-sm text-slate-500">No conversations yet.</p> : null}
        {groups.map((group) => (
          <div key={group.label} className="mt-2">
            <h3 className="px-2 text-xs font-semibold uppercase tracking-wide text-slate-400">{group.label}</h3>
            <ul className="mt-1 space-y-0.5">
              {group.items.map((item) => (
                <li key={item.conversation_id} className="group flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => selectConversation(item.conversation_id)}
                    aria-current={item.conversation_id === activeId ? "true" : undefined}
                    className={`min-w-0 flex-1 truncate rounded-lg px-2 py-2 text-left text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                      item.conversation_id === activeId ? "bg-indigo-50 font-medium text-indigo-900" : "text-slate-700 hover:bg-slate-100"
                    }`}
                  >
                    {maskSensitive(item.title) || "Untitled conversation"}
                  </button>
                  {archiveCandidate === item.conversation_id ? (
                    <span className="flex shrink-0 gap-1">
                      <Button type="button" size="sm" variant="outline" onClick={() => void archive(item.conversation_id)}>Archive</Button>
                      <Button type="button" size="sm" variant="outline" onClick={() => setArchiveCandidate(null)}>Keep</Button>
                    </span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setArchiveCandidate(item.conversation_id)}
                      aria-label={`Archive conversation: ${maskSensitive(item.title)}`}
                      className="shrink-0 rounded-md p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      <Archive className="h-4 w-4" aria-hidden="true" />
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </nav>
  );

  return (
    <div className="flex h-[calc(100vh-4.5rem)] min-h-[32rem] overflow-hidden rounded-xl border border-border bg-white shadow-sm">
      <aside className="hidden w-72 shrink-0 border-r border-border bg-slate-50/60 lg:block">{conversationList}</aside>
      {listOpen ? (
        <div className="fixed inset-0 z-40 flex lg:hidden" role="dialog" aria-modal="true" aria-label="Conversations">
          <div className="w-80 max-w-[85vw] bg-white shadow-xl">{conversationList}</div>
          <button type="button" aria-label="Close conversations" className="flex-1 bg-slate-900/30" onClick={() => setListOpen(false)} />
        </div>
      ) : null}

      <section className="flex min-w-0 flex-1 flex-col" aria-label="PeopleOps Copilot conversation">
        <header className="flex items-center gap-2 border-b border-border px-4 py-3">
          <Button type="button" variant="outline" size="sm" className="lg:hidden" onClick={() => setListOpen(true)} aria-expanded={listOpen}>
            <PanelLeft className="mr-1 h-4 w-4" aria-hidden="true" />
            Conversations
          </Button>
          <Sparkles className="h-5 w-5 text-indigo-500" aria-hidden="true" />
          <h2 className="truncate text-base font-semibold text-slate-900">NovaWorks PeopleOps Copilot</h2>
        </header>

        <div className="flex-1 overflow-y-auto px-4 py-4">
          {banner ? <p role="alert" className="mb-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{banner}</p> : null}
          {loadingConversation ? <p className="text-sm text-slate-500" role="status">Loading conversation…</p> : null}
          {!loadingConversation && messages.length === 0 ? <EmptyState roleHint={roleHint} capabilities={capabilities} onPick={(prompt) => void send(prompt)} disabled={sending} /> : null}

          <ol className="space-y-4" aria-label="Conversation messages">
            {messages.map((message) => (
              <li key={message.id}>
                <MessageView message={message} actionStates={actionStates} onConfirm={confirm} onCancel={cancel} onRetry={(text) => void send(text)} sending={sending} />
              </li>
            ))}
          </ol>
          <div role="status" aria-live="polite" className="mt-3 min-h-[1.5rem] text-sm text-slate-500">
            {sending ? (
              <span className="inline-flex items-center gap-2">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                PeopleOps Copilot is thinking…
              </span>
            ) : null}
          </div>
          <div ref={endRef} />
        </div>

        <form
          className="border-t border-border p-3"
          onSubmit={(event) => {
            event.preventDefault();
            void send(input);
          }}
        >
          <label htmlFor="copilot-input" className="sr-only">Ask PeopleOps Copilot</label>
          <div className="flex items-end gap-2">
            <textarea
              id="copilot-input"
              ref={inputRef}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={onKeyDown}
              rows={2}
              maxLength={MAX_MESSAGE}
              placeholder="Ask PeopleOps Copilot…"
              aria-describedby="copilot-input-hint"
              className="min-h-[2.75rem] flex-1 resize-y rounded-lg border border-border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
            <Button type="submit" disabled={sending || !input.trim()} className="gap-2">
              <Send className="h-4 w-4" aria-hidden="true" />
              Send
            </Button>
          </div>
          <p id="copilot-input-hint" className="mt-1 text-xs text-slate-500">
            Enter to send · Shift+Enter for a new line · {input.length}/{MAX_MESSAGE}
          </p>
        </form>
      </section>
    </div>
  );
}

function EmptyState({ roleHint, capabilities, onPick, disabled }: {
  roleHint: string | null;
  capabilities?: string[] | null;
  onPick: (prompt: string) => void;
  disabled: boolean;
}) {
  const mode = copilotMode(roleHint, capabilities); // UX only: the backend authorizes every request
  return (
    <div className="mx-auto max-w-xl py-8 text-center">
      <Sparkles className="mx-auto h-8 w-8 text-indigo-500" aria-hidden="true" />
      <h3 className="mt-2 text-lg font-semibold text-slate-900">PeopleOps Copilot</h3>
      <p className="mt-1 text-sm font-medium text-indigo-700">{mode.title}</p>
      <ul aria-label={`${mode.title} focus areas`} className="mt-2 flex flex-wrap justify-center gap-x-3 gap-y-1 text-xs text-slate-500">
        {mode.focus.map((area) => <li key={area}>{area}</li>)}
      </ul>
      <p className="mt-2 text-sm text-slate-600">
        Ask about HR policies, your HR data, or HR actions you're allowed to perform. Actions always need your confirmation.
      </p>
      <p className="mt-4 text-xs font-semibold uppercase tracking-wide text-slate-400">Try</p>
      <ul className="mt-2 flex flex-wrap justify-center gap-2">
        {mode.prompts.map((prompt) => (
          <li key={prompt}>
            <button
              type="button"
              disabled={disabled}
              onClick={() => onPick(prompt)}
              className="rounded-full border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-sm text-indigo-800 hover:bg-indigo-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
            >
              {prompt}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

const HISTORY_LABELS: Record<string, string> = {
  refused: "Can't help with that",
  clarification_required: "Needs clarification",
  confirmation_required: "Action proposed",
  error: "Couldn't answer",
};

function MessageView({
  message,
  actionStates,
  onConfirm,
  onCancel,
  onRetry,
  sending,
}: {
  message: UiMessage;
  actionStates: Record<string, ActionState>;
  onConfirm: (id: string) => void;
  onCancel: (id: string) => void;
  onRetry: (text: string) => void;
  sending: boolean;
}) {
  if (message.kind === "user") {
    return (
      <article className="ml-auto max-w-[85%] rounded-2xl rounded-br-sm bg-indigo-600 px-4 py-2.5 text-white" aria-label="Your message">
        <p className="sr-only">You</p>
        <SafeText text={message.text} className="whitespace-pre-wrap break-words text-sm" />
      </article>
    );
  }
  if (message.kind === "error") {
    return (
      <article role="alert" className="max-w-[85%] rounded-2xl rounded-bl-sm border border-red-200 bg-red-50 px-4 py-2.5" aria-label="Copilot error">
        <p className="text-sm text-red-800">{message.text}</p>
        <div className="mt-1 flex items-center gap-3 text-xs text-red-700">
          {message.reference ? <span>Reference: {message.reference}</span> : null}
          {message.retryText ? (
            <button type="button" disabled={sending} onClick={() => onRetry(message.retryText!)} className="font-medium underline underline-offset-2 disabled:opacity-50">
              Try again
            </button>
          ) : null}
        </div>
      </article>
    );
  }
  const label = <p className="mb-1 text-xs font-semibold text-indigo-700">PeopleOps Copilot</p>;
  if (message.kind === "history") {
    const { status, content, error_code: errorCode } = message.message;
    const statusLabel = status ? HISTORY_LABELS[status] : undefined;
    const text = status === "error" ? friendlyError(errorCode ? { code: errorCode, message: "" } : undefined, 500) : content;
    return (
      <article className="max-w-[85%] rounded-2xl rounded-bl-sm border border-slate-200 bg-white px-4 py-2.5 shadow-sm" aria-label="Copilot message">
        {label}
        {statusLabel ? <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">{statusLabel}</p> : null}
        <SafeText text={text} />
        {status === "confirmation_required" ? (
          <p className="mt-2 text-xs text-slate-500">This proposal can only be confirmed right after it was made. Ask again to get a new confirmation.</p>
        ) : null}
      </article>
    );
  }
  return (
    <article className="max-w-[85%] space-y-3 rounded-2xl rounded-bl-sm border border-slate-200 bg-white px-4 py-2.5 shadow-sm" aria-label="Copilot message">
      {label}
      {message.parts.map((part, index) => (
        <PartView key={index} part={part} actionStates={actionStates} onConfirm={onConfirm} onCancel={onCancel} />
      ))}
    </article>
  );
}
