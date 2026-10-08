"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { CellType } from "@/components/CellType";
import { Button, ErrorNote, Wordmark } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { COUNTRIES } from "@/lib/format";

export function AuthForm({ mode }: { mode: "login" | "register" }) {
  const router = useRouter();
  const { user, setUser } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [country, setCountry] = useState("IN");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const register = mode === "register";

  useEffect(() => {
    if (user) router.replace("/app");
  }, [user, router]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    if (register && password.length < 10) {
      setError("Choose a password of at least 10 characters.");
      return;
    }
    setBusy(true);
    try {
      const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
      const signedIn = register
        ? await api.register({ email, password, country, timezone })
        : await api.login({ email, password });
      setUser(signedIn);
      router.replace("/app");
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  };

  return (
    <>
      <header className="nav">
        <Wordmark />
        <div className="nav-end">
          <Link className="chip" href={register ? "/login" : "/register"}>
            {register ? "Have an account? Sign in" : "New here? Create account"}
          </Link>
        </div>
      </header>
      <main className="auth wrap">
        <div className="stack">
          <CellType as="h1" className="auth-type" lines={register ? ["START", "WATCHING"] : ["WELCOME", "BACK"]} fills={[undefined, "var(--pen)"]} />
          <p className="lede">
            {register
              ? "Pick your country so every price and forecast is in your own currency. You can change it later."
              : "Sign in to see your watchlist, forecasts and alerts."}
          </p>
        </div>
        <form onSubmit={submit} noValidate>
          <h2 className="h-sm">{register ? "Create your account" : "Sign in"}</h2>
          <label className="field">
            <span>Email</span>
            <input className="input" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </label>
          <label className="field">
            <span>Password</span>
            <input
              className="input"
              type="password"
              autoComplete={register ? "new-password" : "current-password"}
              required
              minLength={register ? 10 : 1}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              aria-describedby={register ? "pw-hint" : undefined}
            />
            {register ? <small id="pw-hint">At least 10 characters.</small> : null}
          </label>
          {register ? (
            <label className="field">
              <span>Country for prices</span>
              <select className="select" value={country} onChange={(e) => setCountry(e.target.value)}>
                {COUNTRIES.map((c) => (
                  <option key={c.code} value={c.code}>{c.name}</option>
                ))}
              </select>
            </label>
          ) : null}
          {error ? <ErrorNote>{error}</ErrorNote> : null}
          <Button type="submit" variant="hi" disabled={busy || !email || !password}>
            {busy ? "One moment" : register ? "Create account" : "Sign in"}
          </Button>
        </form>
      </main>
    </>
  );
}
