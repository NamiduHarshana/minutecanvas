import type { Metadata } from "next";
import { AppShell } from "@/components/AppShell";
import { Account } from "@/components/pages/Account";

export const metadata: Metadata = {
  title: "Account — MinuteCanvas",
  description: "Your Google sign-in, theme and data.",
  robots: { index: false, follow: false },
};

export default function Page() {
  return (
    <AppShell active="account">
      <Account />
    </AppShell>
  );
}
