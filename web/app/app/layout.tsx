"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { AttributionNote, Skeleton, Wordmark } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const LINKS = [
  { href: "/app", label: "Search" },
  { href: "/app/watchlist", label: "Watchlist" },
  { href: "/app/alerts", label: "Alerts" },
  { href: "/app/scorecard", label: "Scorecard" },
  { href: "/app/account", label: "Account" },
];

export default function AppLayout({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [sent, setSent] = useState(false);

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [loading, user, router]);

  if (loading || !user) {
    return (
      <>
        <header className="nav">
          <Wordmark />
        </header>
        <main className="page wrap" aria-busy="true">
          <Skeleton height="10rem" />
        </main>
      </>
    );
  }

  const isCurrent = (href: string) => (href === "/app" ? pathname === "/app" || pathname.startsWith("/app/game") : pathname.startsWith(href));

  return (
    <>
      <header className="nav">
        <Wordmark href="/app" />
        <nav className="nav-links" aria-label="App">
          {LINKS.map((link) => (
            <Link key={link.href} href={link.href} aria-current={isCurrent(link.href) ? "page" : undefined}>
              {link.label}
            </Link>
          ))}
        </nav>
        <div className="nav-end">
          <Link className="chip" href="/app/account" title="Prices are shown for this country. Change it in Account.">
            {user.default_country} · {user.default_currency}
          </Link>
        </div>
      </header>
      {!user.email_verified ? (
        <div className="banner" role="status">
          <span>Confirm {user.email} so email alerts can reach you.</span>
          <button
            type="button"
            className="link"
            style={{ background: "none", border: 0, padding: 0, cursor: "pointer" }}
            disabled={sent}
            onClick={() => api.resendVerification().finally(() => setSent(true))}
          >
            {sent ? "Link sent, check your inbox" : "Send the link again"}
          </button>
        </div>
      ) : null}
      {children}
      <footer className="footer wrap">
        <p>Forecasts and recommendations are statistical estimates based on past prices. They are not guarantees and not financial advice.</p>
        <AttributionNote />
      </footer>
    </>
  );
}
