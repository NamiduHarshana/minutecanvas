import type { Metadata } from "next";
import Replay from "@/components/Replay";

export const metadata: Metadata = {
  title: "Meeting replay — MinuteCanvas",
  description: "Watch a meeting's transcript, board and Live Flow Map build up again, from 00:00.",
  robots: { index: false, follow: false },
};

export default function ReplayPage() {
  return <Replay />;
}
