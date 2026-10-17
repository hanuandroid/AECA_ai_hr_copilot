import type { Metadata } from "next";

import { AccessGuard } from "@/components/access/access-guard";

import "./globals.css";

export const metadata: Metadata = {
  title: "CB Nest",
  description: "Mock HRMS frontend scaffold",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body suppressHydrationWarning>
        <AccessGuard>{children}</AccessGuard>
      </body>
    </html>
  );
}
