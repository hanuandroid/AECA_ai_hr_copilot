// G-11 phase 2: the admin UI asks for the administrator's password before high-impact changes and sends it only in the
// X-Step-Up-Password header. UX only - the backend decides (backend tests/rbac/test_step_up_api.py). Synthetic values.
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AnnouncementsPage from "@/app/announcements/page";
import NewEmployeePage from "@/app/employees/new/page";
import EmployeesPage from "@/app/employees/page";
import PollsPage from "@/app/polls/page";
import { employeeUpdateNeedsStepUp, type EmployeeSensitiveSnapshot } from "@/lib/step-up";

import { fail, mockBackend, ok, type RecordedCall } from "./helpers";

// A stable router object, as in Next.js: the employee form reloads its data whenever `router` changes identity.
const nav = vi.hoisted(() => {
  const push = vi.fn();
  return { params: new URLSearchParams(), push, router: { push, replace: push } };
});
vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  usePathname: () => "/",
  useSearchParams: () => nav.params,
}));
vi.mock("@/components/layout/sidebar", () => ({ Sidebar: () => null }));
vi.mock("@/components/layout/topbar", () => ({ Topbar: () => null }));
// The calendar widget is not under test; a plain input keeps the form fillable.
vi.mock("@/components/ui/date-picker", () => ({
  DatePicker: ({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder?: string }) => (
    <input aria-label={placeholder} value={value} onChange={(e) => onChange(e.target.value)} />
  ),
}));

const PW = "Synthetic-Admin-Pass-42";
const TEMP_PW = "Synthetic-Temp-8"; // the new account's synthetic temporary password
const HEADER = "X-Step-Up-Password";
const admin = () => ok({ id: 1, name: "Admin Test", role: "ADMIN", has_profile_photo: false });
let logs: ReturnType<typeof vi.spyOn>[] = [];

beforeEach(() => {
  localStorage.setItem("hrms_access_token", "token");
  nav.params = new URLSearchParams();
  nav.push.mockReset();
  logs = [vi.spyOn(console, "log"), vi.spyOn(console, "info"), vi.spyOn(console, "warn"), vi.spyOn(console, "error")];
});
afterEach(() => vi.restoreAllMocks());

const posts = (calls: RecordedCall[], method = "POST") => calls.filter((c) => c.method === method);

/** The password must only ever travel in the header of the confirmed request. */
function assertPasswordOnlyInHeader(calls: RecordedCall[]) {
  for (const call of calls) {
    expect(call.path).not.toContain(PW);
    expect(JSON.stringify(call.body ?? {})).not.toContain(PW);
  }
  const stored = [JSON.stringify({ ...localStorage }), JSON.stringify({ ...sessionStorage }), document.cookie];
  for (const value of stored) expect(value).not.toContain(PW);
  for (const spy of logs) expect(JSON.stringify(spy.mock.calls)).not.toContain(PW);
  expect(document.body.innerHTML).not.toContain(PW);
}

async function confirmWith(password: string) {
  const dialog = await screen.findByRole("dialog");
  await userEvent.type(within(dialog).getByLabelText("Your password"), password);
  await userEvent.click(within(dialog).getByRole("button", { name: "Confirm" }));
}

// --- announcements and polls ---------------------------------------------------------------------------------------

function announcementBackend(...replies: Array<() => ReturnType<typeof ok>>) {
  let index = 0;
  return mockBackend({
    "GET /api/v1/employees/me": admin,
    "GET /api/v1/announcements": () => ok({ items: [], meta: { total: 0, limit: 20, offset: 0 } }),
    "POST /api/v1/announcements": () => replies[Math.min(index++, replies.length - 1)](),
  });
}

async function writeAnnouncement(audience: "ORG" | "TEAM" = "ORG") {
  render(<AnnouncementsPage />);
  await screen.findByRole("combobox", { name: "Audience" });
  if (audience === "TEAM") await userEvent.selectOptions(screen.getByRole("combobox", { name: "Audience" }), "TEAM");
  await userEvent.type(screen.getByPlaceholderText("Title"), "Townhall");
  await userEvent.type(screen.getByPlaceholderText(/Announcement details/), "Townhall moved to 5 PM.");
  await userEvent.click(screen.getByRole("button", { name: "Post Announcement" }));
}

describe("announcements", () => {
  it("asks before an ORG post, sends the password only in the header, then closes and forgets it", async () => {
    const { calls } = announcementBackend(() => ok({ id: 9 }));
    await writeAnnouncement("ORG");
    expect(await screen.findByRole("dialog", { name: "Post to everyone" })).toBeInTheDocument();
    expect(posts(calls)).toHaveLength(0); // nothing is sent before the password is confirmed
    await confirmWith(PW);
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0].headers[HEADER]).toBe(PW);
    expect(posts(calls)[0].body).toEqual({ title: "Townhall", body: "Townhall moved to 5 PM.", audience: "ORG" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    assertPasswordOnlyInHeader(calls);
  });

  it("TEAM posts are sent directly, without a prompt or header", async () => {
    const { calls } = announcementBackend(() => ok({ id: 9 }));
    await writeAnnouncement("TEAM");
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(posts(calls)[0].headers[HEADER]).toBeUndefined();
    expect(posts(calls)[0].body).toMatchObject({ audience: "TEAM" });
  });

  it("a refused password keeps the dialog open with a clear message and a cleared field", async () => {
    const { calls } = announcementBackend(() => fail(403, "STEP_UP_FAILED"), () => ok({ id: 9 }));
    await writeAnnouncement("ORG");
    await confirmWith("not-the-password");
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Your password could not be verified. Nothing was changed.");
    expect(within(dialog).getByLabelText("Your password")).toHaveValue("");
    expect(screen.queryByText("server message must not be shown")).not.toBeInTheDocument();
    await confirmWith(PW); // a new, user-initiated attempt
    await waitFor(() => expect(posts(calls)).toHaveLength(2));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it.each([
    [fail(429, "STEP_UP_THROTTLED"), "Too many password attempts. Wait a minute and try again."],
    [{ status: 403, body: { detail: { success: false, data: null, error: { code: "STEP_UP_REQUIRED", message: "x" } } } },
     "Enter your password to confirm this change."],
  ])("maps the backend's step-up refusals %#", async (reply, text) => {
    announcementBackend(() => reply as ReturnType<typeof ok>);
    await writeAnnouncement("ORG");
    await confirmWith(PW);
    expect(await within(await screen.findByRole("dialog")).findByRole("alert")).toHaveTextContent(text);
  });

  it("a network failure closes the dialog, explains, and never resubmits", async () => {
    const { calls } = announcementBackend(() => ({ networkError: true }) as unknown as ReturnType<typeof ok>);
    await writeAnnouncement("ORG");
    await confirmWith(PW);
    expect(await screen.findByText(/don't know whether this change was applied/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(posts(calls)).toHaveLength(1);
  });

  it("cancel sends nothing and the next prompt starts empty", async () => {
    const { calls } = announcementBackend(() => ok({ id: 9 }));
    await writeAnnouncement("ORG");
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Your password"), PW);
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Post Announcement" }));
    expect(within(await screen.findByRole("dialog")).getByLabelText("Your password")).toHaveValue("");
    expect(posts(calls)).toHaveLength(0);
    assertPasswordOnlyInHeader(calls);
  });
});

describe("polls", () => {
  function pollBackend() {
    return mockBackend({
      "GET /api/v1/employees/me": admin,
      "GET /api/v1/polls": () => ok({ items: [], meta: { total: 0, limit: 20, offset: 0 } }),
      "POST /api/v1/polls": () => ok({ id: 3 }),
    });
  }

  it.each([["ORG", true], ["TEAM", false]] as const)("an %s poll prompts: %s", async (audience, prompts) => {
    const { calls } = pollBackend();
    render(<PollsPage />);
    await screen.findByRole("combobox", { name: "Audience" });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Audience" }), audience);
    await userEvent.type(screen.getByPlaceholderText(/question/i), "Which venue for lunch?");
    await userEvent.type(screen.getByPlaceholderText(/option/i), "Cafe, Garden");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    if (prompts) await confirmWith(PW);
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0].headers[HEADER]).toBe(prompts ? PW : undefined);
    expect(posts(calls)[0].body).toMatchObject({ audience });
    assertPasswordOnlyInHeader(calls);
  });
});

// --- deactivate / reactivate -----------------------------------------------------------------------------------------

describe("employee deactivation and reactivation", () => {
  const person = (status: string) => ({ id: 7, name: "Riya Test", email: "riya@hrms-test.dev", department_id: null,
                                        role: "EMPLOYEE", status, joining_date: "2026-01-01", has_profile_photo: false });

  function employeesBackend(status: string, mutation = () => ok({ id: 7, status: status === "ACTIVE" ? "INACTIVE" : "ACTIVE" })) {
    return mockBackend({
      "GET /api/v1/employees/me": admin,
      "GET /api/v1/employees/departments": () => ok([]),
      "GET /api/v1/employees": () => ok({ items: [person(status)], meta: { total: 1, limit: 10, offset: 0 } }),
      "GET /api/v1/employees/7": () => ok(person(status)),
      "DELETE /api/v1/employees/7": mutation,
      "PATCH /api/v1/employees/7/reactivate": mutation,
    });
  }

  it.each([["ACTIVE", "Deactivate Employee", "DELETE"], ["INACTIVE", "Reactivate Employee", "PATCH"]] as const)(
    "%s employee: %s asks first and sends the header", async (status, button, method) => {
      const { calls } = employeesBackend(status);
      render(<EmployeesPage />);
      await userEvent.click(await screen.findByText("Riya Test"));
      await userEvent.click(await screen.findByRole("button", { name: button }));
      expect(posts(calls, method)).toHaveLength(0);
      await confirmWith(PW);
      await waitFor(() => expect(posts(calls, method)).toHaveLength(1));
      expect(posts(calls, method)[0].headers[HEADER]).toBe(PW);
      assertPasswordOnlyInHeader(calls);
    });
});

// --- employee create / edit ------------------------------------------------------------------------------------------

const DETAILS = {
  id: 7, name: "Riya Test", email: "riya@hrms-test.dev", department_id: null, role: "EMPLOYEE", status: "ACTIVE",
  joining_date: "2026-01-01", date_of_birth: "1995-05-05", job_title: "Engineer", phone: "+919000000001",
  occupancy: "Full-Time", current_salary_usd: 50000, bank_name: "Test Bank", bank_account_number: "000111222333",
  bank_account_name: "Riya Test", bank_branch: "Main", bank_ifsc: "TEST0001234", pan_number: "ABCDE1234Z",
  pan_name: "Riya Test", pan_dob: "1995-05-05", pf_uan: "PFUAN123", esi_no: "ESINO123",
};

function formBackend() {
  return mockBackend({
    "GET /api/v1/employees/me": admin,
    "GET /api/v1/employees/departments": () => ok([]),
    "GET /api/v1/employees/7": () => ok(DETAILS),
    "PUT /api/v1/employees/7": () => ok({ id: 7, status: "ACTIVE" }),
    "POST /api/v1/employees": () => ok({ id: 8 }),
  });
}

async function editAndSave(change?: (user: ReturnType<typeof userEvent.setup>) => Promise<void>) {
  nav.params = new URLSearchParams("employee_id=7");
  const user = userEvent.setup();
  render(<NewEmployeePage />);
  await waitFor(() => expect(screen.getByPlaceholderText("e.g. john@company.com")).toHaveValue(DETAILS.email));
  if (change) await change(user);
  await user.click(screen.getByRole("button", { name: "Continue" }));
  await user.click(await screen.findByRole("button", { name: "Save Changes" }));
}

describe("employee edit", () => {
  it.each([
    ["re-submitting the loaded values", undefined],
    ["a name change", async (u: ReturnType<typeof userEvent.setup>) => {
      await u.clear(screen.getByPlaceholderText("e.g. John Miller"));
      await u.type(screen.getByPlaceholderText("e.g. John Miller"), "Riya Renamed");
    }],
    ["an email that only differs in case", async (u: ReturnType<typeof userEvent.setup>) => {
      await u.clear(screen.getByPlaceholderText("e.g. john@company.com"));
      await u.type(screen.getByPlaceholderText("e.g. john@company.com"), "RIYA@hrms-test.dev");
    }],
  ])("%s is saved without a prompt", async (_label, change) => {
    const { calls } = formBackend();
    await editAndSave(change);
    await waitFor(() => expect(posts(calls, "PUT")).toHaveLength(1));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(posts(calls, "PUT")[0].headers[HEADER]).toBeUndefined();
  });

  it.each([
    ["email", async (u: ReturnType<typeof userEvent.setup>) => {
      await u.clear(screen.getByPlaceholderText("e.g. john@company.com"));
      await u.type(screen.getByPlaceholderText("e.g. john@company.com"), "riya.new@hrms-test.dev");
    }],
    ["password reset", async (u: ReturnType<typeof userEvent.setup>) => {
      await u.type(screen.getByPlaceholderText("Leave blank to keep current password"), "Synthetic-Reset-9");
    }],
  ])("a sensitive change (%s) asks first and sends the header", async (_label, change) => {
    const { calls } = formBackend();
    await editAndSave(change);
    expect(await screen.findByRole("dialog", { name: "Confirm sensitive changes" })).toBeInTheDocument();
    expect(posts(calls, "PUT")).toHaveLength(0);
    await confirmWith(PW);
    await waitFor(() => expect(posts(calls, "PUT")).toHaveLength(1));
    expect(posts(calls, "PUT")[0].headers[HEADER]).toBe(PW);
    expect(JSON.stringify(posts(calls, "PUT")[0].body)).not.toContain(PW);
    assertPasswordOnlyInHeader(calls);
  });

  it("when the backend still asks for the password, the dialog opens and nothing is resent until confirmed", async () => {
    const replies = [() => fail(403, "STEP_UP_REQUIRED"), () => ok({ id: 7, status: "ACTIVE" })];
    let index = 0;
    const { calls } = mockBackend({
      "GET /api/v1/employees/me": admin,
      "GET /api/v1/employees/departments": () => ok([]),
      "GET /api/v1/employees/7": () => ok(DETAILS),
      "PUT /api/v1/employees/7": () => replies[Math.min(index++, replies.length - 1)](),
    });
    await editAndSave();
    const dialog = await screen.findByRole("dialog", { name: "Confirm sensitive changes" });
    expect(within(dialog).getByText(/Enter your password to confirm this change/)).toBeInTheDocument();
    expect(posts(calls, "PUT")).toHaveLength(1);
    expect(posts(calls, "PUT")[0].headers[HEADER]).toBeUndefined();
    await confirmWith(PW);
    await waitFor(() => expect(posts(calls, "PUT")).toHaveLength(2));
    expect(posts(calls, "PUT")[1].headers[HEADER]).toBe(PW);
    assertPasswordOnlyInHeader(calls);
  });
});

async function fillNewEmployee(user: ReturnType<typeof userEvent.setup>) {
  const fill = async (placeholder: string, value: string) => user.type(screen.getByPlaceholderText(placeholder), value);
  await fill("e.g. John Miller", "New Hire");
  await fill("e.g. john@company.com", "new.hire@hrms-test.dev");
  await fill("Minimum 8 characters", TEMP_PW);
  await user.type(screen.getByLabelText("Select joining date"), "2026-10-01");
  await user.type(screen.getByLabelText("Select date of birth"), "1996-06-06");
  await fill("e.g. Software Engineer", "Engineer");
  await fill("Local number", "9000000002");
  await user.selectOptions(screen.getByDisplayValue("Select employment type"), "Full-Time");
  await user.click(screen.getByRole("button", { name: "Continue" }));
  await fill("e.g. 65000", "50000");
  await fill("e.g. HDFC Bank", "Test Bank");
  await fill("e.g. 123456789012", "000111222444");
  await fill("As per bank records", "New Hire");
  await fill("e.g. City Center", "Main");
  await fill("e.g. HDFC0001234", "TEST0001234");
  await fill("e.g. ABCDE1234F", "ABCDE1234Y");
  await fill("As per PAN card", "New Hire");
  await user.type(screen.getByLabelText("Select PAN DOB"), "1996-06-06");
  await fill("Provident Fund UAN", "PFUAN456");
  await fill("Employee State Insurance Number", "ESINO456");
}

describe("employee create", () => {
  it("always asks, keeps existing validation, and sends the header", async () => {
    const { calls } = formBackend();
    const user = userEvent.setup();
    render(<NewEmployeePage />);
    await screen.findByPlaceholderText("e.g. John Miller");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByText(/are required/)).toBeInTheDocument(); // existing validation still runs first
    await fillNewEmployee(user);
    await user.click(screen.getByRole("button", { name: "Create Employee" }));
    expect(await screen.findByRole("dialog", { name: "Create employee account" })).toBeInTheDocument();
    expect(posts(calls)).toHaveLength(0);
    await confirmWith(PW);
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0].headers[HEADER]).toBe(PW);
    expect(posts(calls)[0].body).toMatchObject({ email: "new.hire@hrms-test.dev", password: TEMP_PW });
    assertPasswordOnlyInHeader(calls);
  });
});

// --- failure paths on every protected operation ----------------------------------------------------------------------

type Reply = ReturnType<typeof ok>;
const NETWORK_DOWN = { networkError: true } as unknown as Reply;

/** Replies to the protected request in order; the last reply repeats. */
function inOrder(replies: Reply[]) {
  let index = 0;
  return () => replies[Math.min(index++, replies.length - 1)];
}

const listed = (status: string) => ({ id: 7, name: "Riya Test", email: "riya@hrms-test.dev", department_id: null,
                                      role: "EMPLOYEE", status, joining_date: "2026-01-01", has_profile_photo: false });

type Operation = { method: string; dialog: string; open: (replies: Reply[]) => Promise<RecordedCall[]> };

const OPERATIONS: Record<string, Operation> = {
  "ORG announcement": {
    method: "POST", dialog: "Post to everyone",
    open: async (replies) => {
      const { calls } = announcementBackend(...replies.map((reply) => () => reply));
      await writeAnnouncement("ORG");
      return calls;
    },
  },
  "ORG poll": {
    method: "POST", dialog: "Create a poll for everyone",
    open: async (replies) => {
      const { calls } = mockBackend({
        "GET /api/v1/employees/me": admin,
        "GET /api/v1/polls": () => ok({ items: [], meta: { total: 0, limit: 20, offset: 0 } }),
        "POST /api/v1/polls": inOrder(replies),
      });
      render(<PollsPage />);
      await screen.findByRole("combobox", { name: "Audience" });
      await userEvent.type(screen.getByPlaceholderText("Question"), "Which venue for lunch?");
      await userEvent.type(screen.getByPlaceholderText("Option A, Option B"), "Cafe, Garden");
      await userEvent.click(screen.getByRole("button", { name: "Create" }));
      return calls;
    },
  },
  ...Object.fromEntries((["ACTIVE", "INACTIVE"] as const).map((status) => {
    const deactivate = status === "ACTIVE";
    return [deactivate ? "deactivation" : "reactivation", {
      method: deactivate ? "DELETE" : "PATCH", dialog: deactivate ? "Deactivate employee" : "Reactivate employee",
      open: async (replies: Reply[]) => {
        const { calls } = mockBackend({
          "GET /api/v1/employees/me": admin,
          "GET /api/v1/employees/departments": () => ok([]),
          "GET /api/v1/employees": () => ok({ items: [listed(status)], meta: { total: 1, limit: 10, offset: 0 } }),
          "GET /api/v1/employees/7": () => ok(listed(status)),
          [deactivate ? "DELETE /api/v1/employees/7" : "PATCH /api/v1/employees/7/reactivate"]: inOrder(replies),
        });
        render(<EmployeesPage />);
        await userEvent.click(await screen.findByText("Riya Test"));
        await userEvent.click(await screen.findByRole("button", { name: deactivate ? "Deactivate Employee" : "Reactivate Employee" }));
        return calls;
      },
    }];
  })),
  "sensitive employee edit": {
    method: "PUT", dialog: "Confirm sensitive changes",
    open: async (replies) => {
      const { calls } = mockBackend({
        "GET /api/v1/employees/me": admin,
        "GET /api/v1/employees/departments": () => ok([]),
        "GET /api/v1/employees/7": () => ok(DETAILS),
        "PUT /api/v1/employees/7": inOrder(replies),
      });
      await editAndSave(async (u) => {
        await u.clear(screen.getByPlaceholderText("e.g. john@company.com"));
        await u.type(screen.getByPlaceholderText("e.g. john@company.com"), "riya.moved@hrms-test.dev");
      });
      return calls;
    },
  },
  "employee creation": {
    method: "POST", dialog: "Create employee account",
    open: async (replies) => {
      const { calls } = mockBackend({
        "GET /api/v1/employees/me": admin,
        "GET /api/v1/employees/departments": () => ok([]),
        "POST /api/v1/employees": inOrder(replies),
      });
      const user = userEvent.setup();
      render(<NewEmployeePage />);
      await screen.findByPlaceholderText("e.g. John Miller");
      await fillNewEmployee(user);
      await user.click(screen.getByRole("button", { name: "Create Employee" }));
      return calls;
    },
  },
};

const SUCCESS = ok({ id: 7, status: "ACTIVE" });
const sent = (calls: RecordedCall[], method: string) => calls.filter((call) => call.method === method && call.path !== "/api/v1/employees/me");

describe.each(Object.entries(OPERATIONS))("%s: failure paths", (_name, operation) => {
  it("a wrong password keeps the dialog open with a cleared field; only a new user attempt resends", async () => {
    const calls = await operation.open([fail(403, "STEP_UP_FAILED"), SUCCESS]);
    await confirmWith("Not-The-Password-1");
    const dialog = await screen.findByRole("dialog", { name: operation.dialog });
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Your password could not be verified. Nothing was changed.");
    expect(within(dialog).getByLabelText("Your password")).toHaveValue("");
    expect(sent(calls, operation.method)).toHaveLength(1);
    expect(screen.queryByText("server message must not be shown")).not.toBeInTheDocument();
    await confirmWith(PW);
    await waitFor(() => expect(sent(calls, operation.method)).toHaveLength(2));
    expect(sent(calls, operation.method)[1].headers[HEADER]).toBe(PW);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    assertPasswordOnlyInHeader(calls);
  });

  it("throttling is explained and nothing is retried automatically", async () => {
    const calls = await operation.open([fail(429, "STEP_UP_THROTTLED")]);
    await confirmWith(PW);
    const dialog = await screen.findByRole("dialog", { name: operation.dialog });
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Too many password attempts. Wait a minute and try again.");
    expect(within(dialog).getByLabelText("Your password")).toHaveValue("");
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(sent(calls, operation.method)).toHaveLength(1);
    assertPasswordOnlyInHeader(calls);
  });

  it("a network failure closes the dialog, says the outcome is unknown and never resubmits", async () => {
    const calls = await operation.open([NETWORK_DOWN]);
    await confirmWith(PW);
    expect(await screen.findByText(/don't know whether this change was applied/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(sent(calls, operation.method)).toHaveLength(1);
    assertPasswordOnlyInHeader(calls);
  });

  it("Escape closes the dialog, sends nothing and drops what was typed", async () => {
    const calls = await operation.open([SUCCESS]);
    const dialog = await screen.findByRole("dialog", { name: operation.dialog });
    await userEvent.type(within(dialog).getByLabelText("Your password"), PW);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(sent(calls, operation.method)).toHaveLength(0);
    assertPasswordOnlyInHeader(calls);
  });
});

// --- change detection (mirrors the backend's _update_needs_step_up) --------------------------------------------------

describe("employeeUpdateNeedsStepUp", () => {
  const original: EmployeeSensitiveSnapshot = {
    email: "riya@hrms-test.dev", role: "EMPLOYEE", bank_name: "Test Bank", bank_account_number: "000111222333",
    bank_account_name: "Riya Test", bank_branch: "Main", bank_ifsc: "TEST0001234", pan_number: "ABCDE1234Z",
    pan_name: "Riya Test", pan_dob: "1995-05-05",
  };
  it.each([
    [{}, false],
    [{ name: "Anything", phone: "+910000000000", current_salary_usd: 1 }, false],
    [{ email: " RIYA@HRMS-TEST.DEV " }, false],
    [{ bank_ifsc: "test0001234", pan_number: " abcde1234z ", bank_name: " Test Bank " }, false],
    [{ role: "EMPLOYEE" as const, pan_dob: "1995-05-05", password: "   " }, false],
    [{ email: "other@hrms-test.dev" }, true],
    [{ role: "MANAGER" as const }, true],
    [{ password: "Synthetic-Reset-9" }, true],
    [{ bank_account_number: "999" }, true],
    [{ pan_name: "Someone Else" }, true],
    [{ pan_dob: "1990-01-01" }, true],
  ])("%j -> %s", (payload, expected) => {
    expect(employeeUpdateNeedsStepUp(original, payload)).toBe(expected);
  });
});
