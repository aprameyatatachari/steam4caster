"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { ButtonLink, ErrorNote, Wordmark } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";

function Verify() {
  const token = useSearchParams().get("token");
  const [state, setState] = useState<"working" | "done" | "error">("working");
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (!token) {
      setState("error");
      setMessage("This link is missing its verification token. Open the link from your email again.");
      return;
    }
    api
      .confirmEmail(token)
      .then(() => setState("done"))
      .catch((err) => {
        setState("error");
        setMessage(errorMessage(err));
      });
  }, [token]);

  return (
    <main className="page wrap" style={{ maxWidth: "46rem" }}>
      <h1 className="display h-lg">
        {state === "working" ? "Checking your link" : state === "done" ? "Email confirmed" : "That link did not work"}
      </h1>
      {state === "done" ? <p className="lede">Email alerts can now reach you.</p> : null}
      {state === "error" ? <ErrorNote>{message}</ErrorNote> : null}
      {state !== "working" ? (
        <div className="hero-actions">
          <ButtonLink href="/app" variant="hi">Open the app</ButtonLink>
          {state === "error" ? <Link className="link" href="/app/account">Send a new link from your account</Link> : null}
        </div>
      ) : null}
    </main>
  );
}

export default function VerifyEmailPage() {
  return (
    <>
      <header className="nav">
        <Wordmark />
      </header>
      <Suspense fallback={null}>
        <Verify />
      </Suspense>
    </>
  );
}
