import type { Metadata } from "next";
import { AppShell } from "@/components/AppShell";
import { Findings } from "@/components/pages/Findings";

export const metadata: Metadata = {
  title: "Findings — MinuteCanvas",
  description: "Every gap, conflict and bug Muse Spark caught across your meetings.",
  robots: { index: false, follow: false },
};

export default function Page() {
  return (
    <AppShell active="findings">
      <Findings />
    </AppShell>
  );
}
