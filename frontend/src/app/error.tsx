"use client";

/** Shows the real error text on screen (no browser console needed, e.g. on a phone). */
export default function ErrorPage({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div className="mx-auto max-w-xl p-6 text-sm">
      <h1 className="mb-3 text-lg font-semibold">Что-то пошло не так</h1>
      <pre className="whitespace-pre-wrap break-words rounded-lg border border-line bg-panel p-3 text-bad">
        {String(error?.message || error)}
        {error?.stack ? "\n\n" + error.stack.split("\n").slice(0, 6).join("\n") : ""}
      </pre>
      <div className="mt-4 flex gap-3">
        <button onClick={() => reset()} className="rounded-lg bg-brand px-3 py-2 text-white">Повторить</button>
        <a href="/login" className="rounded-lg border border-line px-3 py-2">Войти снова</a>
      </div>
    </div>
  );
}
