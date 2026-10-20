// F-BUG-1 regression in the real edit form (app/employees/new/page.tsx): what the form shows for a stored phone and what
// it sends back. Before the fix "+919000000123" loaded as "+919" / "000000123" and an edited number saved as
// "+9199000000999". Mocked HTTP only (tests/helpers.ts); synthetic values.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import NewEmployeePage from "@/app/employees/new/page";

import { mockBackend, ok, type RecordedCall } from "./helpers";

const nav = vi.hoisted(() => {
  const push = vi.fn();
  return { params: new URLSearchParams(), router: { push, replace: push } }; // stable router, as in Next.js
});
vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  usePathname: () => "/employees/new",
  useSearchParams: () => nav.params,
}));
vi.mock("@/components/layout/sidebar", () => ({ Sidebar: () => null }));
vi.mock("@/components/layout/topbar", () => ({ Topbar: () => null }));
vi.mock("@/components/ui/date-picker", () => ({
  DatePicker: ({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder?: string }) => (
    <input aria-label={placeholder} value={value} onChange={(e) => onChange(e.target.value)} />
  ),
}));

// Same long two-step form as tests/step-up-admin.test.tsx: allow for full-suite parallel load (F-TEST-1).
vi.setConfig({ testTimeout: 15_000 });

const DETAILS = {
  id: 7, name: "Riya Test", email: "riya@hrms-test.dev", department_id: null, role: "EMPLOYEE", status: "ACTIVE",
  joining_date: "2026-01-01", date_of_birth: "1995-05-05", job_title: "Engineer", occupancy: "Full-Time",
  current_salary_usd: 50000, bank_name: "Test Bank", bank_account_number: "000111222333", bank_account_name: "Riya Test",
  bank_branch: "Main", bank_ifsc: "TEST0001234", pan_number: "ABCDE1234Z", pan_name: "Riya Test", pan_dob: "1995-05-05",
  pf_uan: "PFUAN123", esi_no: "ESINO123",
};

function backend(phone: string | null) {
  return mockBackend({
    "GET /api/v1/employees/me": () => ok({ id: 1, name: "Admin Test", role: "ADMIN", has_profile_photo: false }),
    "GET /api/v1/employees/departments": () => ok([]),
    "GET /api/v1/employees/7": () => ok({ ...DETAILS, phone }),
    "PUT /api/v1/employees/7": () => ok({ id: 7, status: "ACTIVE" }),
  });
}

const localNumber = () => screen.getByPlaceholderText("Local number");
const countryCode = () => screen.getByRole("combobox", { name: /phone number/i });
const putBody = (calls: RecordedCall[]) => calls.filter((call) => call.method === "PUT").at(-1)?.body as Record<string, unknown>;

async function openEdit(phone: string | null) {
  const { calls } = backend(phone);
  nav.params = new URLSearchParams("employee_id=7");
  const user = userEvent.setup();
  render(<NewEmployeePage />);
  await waitFor(() => expect(screen.getByPlaceholderText("e.g. john@company.com")).toHaveValue(DETAILS.email));
  return { calls, user };
}

async function save(user: ReturnType<typeof userEvent.setup>, calls: RecordedCall[]) {
  await user.click(screen.getByRole("button", { name: "Continue" }));
  await user.click(await screen.findByRole("button", { name: "Save Changes" }));
  await waitFor(() => expect(calls.some((call) => call.method === "PUT")).toBe(true));
  return putBody(calls);
}

describe("employee edit form: phone numbers (F-BUG-1)", () => {
  beforeEach(() => localStorage.setItem("hrms_access_token", "token"));

  it("an Indian number loads as +91 and the full local number", async () => {
    await openEdit("+919000000123");
    expect(countryCode()).toHaveValue("+91");
    expect(localNumber()).toHaveValue("9000000123");
  });

  it("a +971 number is not mistaken for +91 (or +97)", async () => {
    await openEdit("+971501234567");
    expect(countryCode()).toHaveValue("+971");
    expect(localNumber()).toHaveValue("501234567");
  });

  it("saving without touching the phone sends the stored number unchanged", async () => {
    const { calls, user } = await openEdit("+919000000123");
    expect((await save(user, calls)).phone).toBe("+919000000123");
  });

  it("changing the local number keeps the correct country code", async () => {
    const { calls, user } = await openEdit("+919000000123");
    await user.clear(localNumber());
    await user.type(localNumber(), "9000000999");
    expect((await save(user, calls)).phone).toBe("+919000000999"); // was "+9199000000999"
  });

  it("changing only the country code is applied to the same local digits", async () => {
    const { calls, user } = await openEdit("+919000000123");
    await user.selectOptions(countryCode(), "+44");
    expect((await save(user, calls)).phone).toBe("+449000000123");
  });

  it.each([["+33123456789"], ["9876543210"]])(
    "an unsupported stored number (%s) is shown as saved, flagged, and sent back unchanged", async (stored) => {
      const { calls, user } = await openEdit(stored);
      expect(localNumber()).toHaveValue(stored);
      expect(screen.getByText(/no supported country code; it is kept as saved unless you edit it/)).toBeInTheDocument();
      expect((await save(user, calls)).phone).toBe(stored); // no invented "+91"
    });

  it("an unsupported number that the admin edits is saved with the selected code (an explicit choice)", async () => {
    const { calls, user } = await openEdit("9876543210");
    await user.clear(localNumber());
    await user.type(localNumber(), "9876543211");
    expect(screen.queryByText(/no supported country code/)).not.toBeInTheDocument();
    expect((await save(user, calls)).phone).toBe("+919876543211");
  });
});
