import type { Metadata } from "next";
import { AppShell } from "@/components/AppShell";
import { Meetings } from "@/components/pages/Meetings";

export const metadata: Metadata = {
  title: "Meetings — MinuteCanvas",
  description: "Every meeting you recorded: replay, report and delete.",
  robots: { index: false, follow: false },
};

export default function Page() {
  return (
    <AppShell active="meetings">
      <Meetings />
    </AppShell>
  );
}
