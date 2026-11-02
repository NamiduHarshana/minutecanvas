import type { Metadata } from "next";
import App from "@/components/App";

export const metadata: Metadata = {
  title: "Start a meeting — MinuteCanvas",
  description: "Record a meeting and watch the board and the Live Flow Map build while people speak.",
};

export default function MeetPage() {
  return <App />;
}
