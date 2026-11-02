import type { Metadata } from "next";
import { AppShell } from "@/components/AppShell";
import { Dashboard } from "@/components/pages/Dashboard";

export const metadata: Metadata = {
  title: "Dashboard — MinuteCanvas",
  description: "Your meetings, and what they missed.",
  robots: { index: false, follow: false },
};

export default function Page() {
  return (
    <AppShell active="dashboard">
      <Dashboard />
    </AppShell>
  );
}
