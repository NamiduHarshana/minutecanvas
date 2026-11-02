"use client";
// Click-to-load: nothing is fetched from YouTube until the visitor presses play (youtube-nocookie.com).
// Before the video exists, the same space links to the demo replay.
import { useState } from "react";
import { Play } from "lucide-react";
import { SITE, demoReplayHref } from "@/core/site";

export function DemoVideo() {
  const [on, setOn] = useState(false);
  const id = SITE.demoVideoId;
  const replay = demoReplayHref();
  if (!id && !replay) return null;

  if (id && on) {
    return (
      <div className="dv dv-on">
        <iframe
          src={`https://www.youtube-nocookie.com/embed/${encodeURIComponent(id)}?autoplay=1&rel=0`}
          title="MinuteCanvas demo video"
          allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
          allowFullScreen
          referrerPolicy="strict-origin-when-cross-origin"
        />
      </div>
    );
  }
  if (id) {
    return (
      <button type="button" className="dv" onClick={() => setOn(true)}>
        <span className="dv-play" aria-hidden>
          <Play size={30} fill="currentColor" />
        </span>
        <span className="dv-text">
          <b>Watch the 3-minute demo</b>
          <small>Plays from YouTube. Nothing loads from YouTube until you press play.</small>
        </span>
      </button>
    );
  }
  return (
    <a className="dv" href={replay}>
      <span className="dv-play" aria-hidden>
        <Play size={30} fill="currentColor" />
      </span>
      <span className="dv-text">
        <b>Watch a demo meeting replay</b>
        <small>A recorded client meeting plays from 00:00: transcript, board and Flow Map, in the order they appeared.</small>
      </span>
    </a>
  );
}
