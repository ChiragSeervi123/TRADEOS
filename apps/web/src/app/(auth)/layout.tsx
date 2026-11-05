import type { ReactNode } from "react";

export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-bg px-4 py-10">
      <div className="w-full max-w-md">
        <div className="mb-6 flex items-center justify-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded bg-accent font-bold text-white">T</div>
          <span className="text-lg font-semibold tracking-wide text-brand">TRADEOS</span>
        </div>
        {children}
        <p className="mt-6 text-center text-xs text-muted">Trade operations for FMCG distributors</p>
      </div>
    </div>
  );
}
