import { describe, expect, it } from "vitest";

import { activeNavHref } from "@/components/layout/sidebar";

const HREFS = ["/dashboard", "/employees", "/me", "/me/documents", "/finance", "/tickets"];

describe("sidebar active item", () => {
  it.each([
    ["/me/documents", "/me/documents"], // only My Documents, not My Profile as well
    ["/me", "/me"],
    ["/employees/42", "/employees"], // sub-pages keep their section selected
    ["/me/documents/7", "/me/documents"],
    ["/dashboard", "/dashboard"],
  ])("%s selects %s", (pathname, expected) => {
    expect(activeNavHref(pathname, HREFS)).toBe(expected);
  });

  it.each(["/meetings", "/financial", "/login", "/"])("%s selects nothing (no prefix-only matches)", (pathname) => {
    expect(activeNavHref(pathname, HREFS)).toBeNull();
  });
});
