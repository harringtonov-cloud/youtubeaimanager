"use client";

import { Badge } from "./ui";

export type PreflightReport = { ready: boolean; checks: { id: string; label: string; ok: boolean; detail: string; blocking: boolean }[] };

export default function Preflight({ report }: { report: PreflightReport }) {
  return (
    <div>
      <div className="mb-3">
        {report.ready ? <Badge tone="ok">Все обязательные проверки пройдены — можно включать автопубликацию</Badge> : <Badge tone="bad">Не готово — исправьте пункты, отмеченные красным</Badge>}
      </div>
      <ul className="divide-y divide-line rounded-lg border border-line">
        {report.checks.map((c) => (
          <li key={c.id} className="flex items-start gap-3 px-3 py-2 text-sm">
            <span className={c.ok ? "text-ok" : c.blocking ? "text-bad" : "text-warn"}>{c.ok ? "✓" : c.blocking ? "✗" : "!"}</span>
            <div className="min-w-0">
              <div>{c.label}{!c.blocking && <span className="ml-2 text-xs text-muted">(необязательно)</span>}</div>
              <div className="break-words text-xs text-muted">{c.detail}</div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
