"use client";

import { useEffect, useState } from "react";
import { api, useApi } from "@/lib/api";

const COOKIE = "yam_channel";

function setChannelCookie(id: number | string) {
  document.cookie = `${COOKIE}=${id}; path=/; max-age=31536000; samesite=lax`;
}

/** Switch between the YouTube channels managed by this installation (each has its own videos and settings). */
export default function ChannelSwitcher() {
  const { data } = useApi<any>("/api/channels");
  const [busy, setBusy] = useState(false);

  // after connecting another channel the backend sends us back with ?channel=<id>: switch to it
  useEffect(() => {
    const url = new URL(window.location.href);
    const ch = url.searchParams.get("channel");
    if (ch && /^\d+$/.test(ch)) {
      setChannelCookie(ch);
      url.searchParams.delete("channel");
      window.location.replace(url.toString());
    }
  }, []);

  const channels: any[] = data?.channels ?? [];
  const current = data?.current ?? channels[0]?.id;

  function choose(id: string) {
    setChannelCookie(id);
    window.location.reload();
  }

  async function addChannel() {
    setBusy(true);
    try {
      const r = await api<{ auth_url: string }>("/api/youtube/connect", { method: "POST" });
      window.location.href = r.auth_url;
    } catch {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2 border-b border-line p-3 text-xs">
      <div className="px-1 text-muted">Канал YouTube</div>
      {channels.length > 0 ? (
        <select
          value={current ?? ""}
          onChange={(e) => choose(e.target.value)}
          className="w-full rounded-lg border border-line bg-bg px-2 py-2 text-sm text-ink"
        >
          {channels.map((c) => (
            <option key={c.id} value={c.id}>
              {c.title}
            </option>
          ))}
        </select>
      ) : (
        <div className="px-1 text-muted">Канал не подключён</div>
      )}
      <button
        onClick={addChannel}
        disabled={busy}
        className="w-full rounded-lg border border-dashed border-line px-2 py-2 text-muted hover:text-ink disabled:opacity-50"
      >
        ＋ Добавить ещё канал
      </button>
    </div>
  );
}
