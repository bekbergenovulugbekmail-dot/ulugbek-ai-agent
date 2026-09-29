import type { Metadata, Viewport } from "next";

import { OperatorGate } from "@/components/auth/OperatorGate";
import { AppShell } from "@/components/layout/AppShell";

import "./globals.css";

export const metadata: Metadata = {
  title: "ULUGBEK AI — Control Center",
  description: "Command centre for the ULUGBEK AI agent.",
};

export const viewport: Viewport = {
  themeColor: "#08080A",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <OperatorGate>
          <AppShell>{children}</AppShell>
        </OperatorGate>
      </body>
    </html>
  );
}
