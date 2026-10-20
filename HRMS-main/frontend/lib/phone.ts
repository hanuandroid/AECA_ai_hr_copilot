// Phone numbers in the employee form: stored as "<country code><digits>" (e.g. "+919000000123"), edited as a country-code
// select plus a local number. Splitting a stored value uses the LONGEST supported code it starts with, never "1-3 digits
// after +" (which read "+919..." as "+919"; defect F-BUG-1). A stored value that does not start with a supported code
// (other countries, or no "+" at all, like some seeded numbers) is reported as unsupported so the form can keep it
// exactly as stored instead of inventing a country code.

export type CountryCodeOption = { label: string; value: string };

export const COUNTRY_CODES: readonly CountryCodeOption[] = [
  { label: "IN (+91)", value: "+91" },
  { label: "US (+1)", value: "+1" },
  { label: "UK (+44)", value: "+44" },
  { label: "AE (+971)", value: "+971" },
  { label: "SG (+65)", value: "+65" },
  { label: "AU (+61)", value: "+61" },
  { label: "DE (+49)", value: "+49" },
];

export const DEFAULT_COUNTRY_CODE = "+91";

export type SplitPhone = { countryCode: string; local: string; supported: boolean };

/** Split a stored phone into a supported country code and the local digits. */
export function splitStoredPhone(
  stored: string | null | undefined,
  codes: readonly CountryCodeOption[] = COUNTRY_CODES,
  fallback: string = DEFAULT_COUNTRY_CODE,
): SplitPhone {
  const phone = (stored ?? "").trim();
  if (!phone) return { countryCode: fallback, local: "", supported: true }; // nothing stored, nothing to preserve
  const code = codes
    .map((option) => option.value)
    .sort((a, b) => b.length - a.length)
    .find((value) => phone.startsWith(value) && /^\d+$/.test(phone.slice(value.length)));
  if (code) return { countryCode: code, local: phone.slice(code.length), supported: true };
  return { countryCode: fallback, local: phone, supported: false };
}

/** The stored form of a country code and a typed local number (non-digits removed). */
export function composePhone(countryCode: string, local: string): string {
  return `${countryCode}${local.replace(/[^\d]/g, "")}`;
}
