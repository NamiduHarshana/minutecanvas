import type { Metadata } from "next";
import { AppShell } from "@/components/AppShell";
import { Usage } from "@/components/pages/Usage";

export const metadata: Metadata = {
  title: "Usage — MinuteCanvas",
  description: "How much Meta AI work went into your meetings.",
  robots: { index: false, follow: false },
};

export default function Page() {
  return (
    <AppShell active="usage">
      <Usage />
    </AppShell>
  );
}
