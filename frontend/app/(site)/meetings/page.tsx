import type { Metadata } from "next";
import { Meetings } from "@/components/site/Meetings";

export const metadata: Metadata = {
  title: "My meetings — MinuteCanvas",
  description: "Your recorded meetings: replay, report and delete.",
  robots: { index: false, follow: false },
};

export default function MeetingsPage() {
  return <Meetings />;
}
