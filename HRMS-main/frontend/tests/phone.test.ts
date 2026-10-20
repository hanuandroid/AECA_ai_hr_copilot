// F-BUG-1 regression (lib/phone.ts): stored phones are split by the longest supported country code; numbers without a
// supported code are reported as unsupported (kept as stored by the form), never given an invented code.
import { describe, expect, it } from "vitest";

import { COUNTRY_CODES, composePhone, splitStoredPhone, type CountryCodeOption } from "@/lib/phone";

describe("splitStoredPhone", () => {
  it.each([
    ["+919000000123", { countryCode: "+91", local: "9000000123", supported: true }], // was "+919" / "000000123"
    ["+912025550123", { countryCode: "+91", local: "2025550123", supported: true }],
    ["+12025550123", { countryCode: "+1", local: "2025550123", supported: true }],
    ["+447700900123", { countryCode: "+44", local: "7700900123", supported: true }],
    ["+971501234567", { countryCode: "+971", local: "501234567", supported: true }], // +971 is not read as +97/+9
    ["+6591234567", { countryCode: "+65", local: "91234567", supported: true }],
    ["+61412345678", { countryCode: "+61", local: "412345678", supported: true }],
    ["+4915112345678", { countryCode: "+49", local: "15112345678", supported: true }],
    ["  +919000000123  ", { countryCode: "+91", local: "9000000123", supported: true }],
  ])("%s -> supported code and local digits", (stored, expected) => {
    expect(splitStoredPhone(stored)).toEqual(expected);
  });

  it.each([
    ["+33123456789"], // France: not offered by the form
    ["9876543210"], // seeded format without a country code
    ["+91 90000 00123"], // separators: not the stored format, keep as is
    ["+91"], // a code without digits
    ["+"],
    ["not a phone"],
  ])("%s is unsupported: kept verbatim as the local value, flagged, default code only for the select", (stored) => {
    expect(splitStoredPhone(stored)).toEqual({ countryCode: "+91", local: stored.trim(), supported: false });
  });

  it.each([[null], [undefined], [""], ["   "]])("%j (nothing stored) -> empty local number", (stored) => {
    expect(splitStoredPhone(stored)).toEqual({ countryCode: "+91", local: "", supported: true });
  });

  it("the longest matching code wins when one supported code is a prefix of another", () => {
    // The shipped list has no such pair (checked below); this pins the rule for when one is added (e.g. +1 / +1242).
    const codes: CountryCodeOption[] = [{ label: "US", value: "+1" }, { label: "BS", value: "+1242" }, { label: "IN", value: "+91" }];
    expect(splitStoredPhone("+12423571234", codes)).toEqual({ countryCode: "+1242", local: "3571234", supported: true });
    expect(splitStoredPhone("+12025550123", codes)).toEqual({ countryCode: "+1", local: "2025550123", supported: true });
    const values = COUNTRY_CODES.map((option) => option.value);
    const prefixPairs = values.flatMap((a) => values.filter((b) => a !== b && b.startsWith(a)).map((b) => `${a}<${b}`));
    expect(prefixPairs).toEqual([]);
  });

  it("round trip: composing a split supported number gives the stored value back", () => {
    for (const stored of ["+919000000123", "+971501234567", "+12025550123", "+4915112345678"]) {
      const split = splitStoredPhone(stored);
      expect(composePhone(split.countryCode, split.local)).toBe(stored);
    }
  });
});

describe("composePhone", () => {
  it("keeps the chosen code and only the digits of the local number", () => {
    expect(composePhone("+91", "90000 00999")).toBe("+919000000999");
    expect(composePhone("+971", "50-123-4567")).toBe("+971501234567");
  });
});
