"use client";

import Link from "next/link";
import { useEffect, useState, type ComponentProps, type ReactNode } from "react";

import type { Attribution, Game } from "@/lib/api";

type ArtSource = Pick<Game, "steam_app_id" | "assets" | "title">;

/** The game's Steam store page, when its Steam app id is known. */
export function steamUrl(game: Pick<Game, "steam_app_id">): string | null {
  return game.steam_app_id ? `https://store.steampowered.com/app/${game.steam_app_id}/` : null;
}

/** Candidate images, best first: Steam's own header art, then the price provider's. */
function artSources(game: ArtSource, size: "wide" | "thumb"): string[] {
  const steam = game.steam_app_id
    ? [`https://cdn.cloudflare.steamstatic.com/steam/apps/${game.steam_app_id}/${size === "wide" ? "header" : "capsule_231x87"}.jpg`]
    : [];
  const a = game.assets ?? {};
  const provider = size === "wide" ? [a.banner600, a.banner400, a.banner300] : [a.banner300, a.banner145, a.banner400];
  return [...steam, ...provider].filter((src): src is string => Boolean(src));
}

/**
 * Game artwork. Tries each source in turn and renders nothing if none loads, so a
 * missing image never leaves a broken frame.
 */
export function GameArt({ game, size = "wide", className }: { game: ArtSource; size?: "wide" | "thumb"; className?: string }) {
  const sources = artSources(game, size);
  const key = sources.join("|");
  const [index, setIndex] = useState(0);
  useEffect(() => setIndex(0), [key]);
  if (index >= sources.length) return null;
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      className={["art", size === "thumb" ? "art-thumb" : "", className ?? ""].filter(Boolean).join(" ")}
      src={sources[index]}
      alt=""
      loading="lazy"
      decoding="async"
      onError={() => setIndex((i) => i + 1)}
    />
  );
}

/** The one permitted diagonal, used as the arrow on every action. */
export function Slash() {
  return (
    <svg className="slash" viewBox="0 0 14 14" aria-hidden="true" focusable="false">
      <path d="M1 13L13 1" stroke="currentColor" strokeWidth="2" fill="none" />
    </svg>
  );
}

type ButtonProps = { variant?: "ink" | "hi" | "line"; size?: "md" | "sm"; children: ReactNode };

const cls = (variant: ButtonProps["variant"], size: ButtonProps["size"], extra?: string) =>
  ["btn", variant === "hi" ? "btn-hi" : variant === "line" ? "btn-line" : "", size === "sm" ? "btn-sm" : "", extra ?? ""]
    .filter(Boolean)
    .join(" ");

export function Button({ variant, size, children, className, ...props }: ButtonProps & ComponentProps<"button">) {
  return (
    <button type="button" {...props} className={cls(variant, size, className)}>
      <span>{children}</span>
      <Slash />
    </button>
  );
}

export function ButtonLink({ variant, size, children, className, ...props }: ButtonProps & ComponentProps<typeof Link>) {
  return (
    <Link {...props} className={cls(variant, size, className)}>
      <span>{children}</span>
      <Slash />
    </Link>
  );
}

export function Wordmark({ href = "/" }: { href?: string }) {
  return (
    <Link href={href} className="wordmark" aria-label="Steam4Caster home">
      <svg viewBox="0 0 5 5" width="18" height="18" aria-hidden="true">
        <path d="M0 4h1V3h1V2h1V1h1V0h1v5H0z" fill="currentColor" />
      </svg>
      <span>
        Steam<i>4</i>Caster
      </span>
    </Link>
  );
}

export function AttributionNote({ attribution }: { attribution?: Attribution | null }) {
  const a = attribution ?? {
    text: "Price data provided by the IsThereAnyDeal API.",
    url: "https://isthereanydeal.com/",
    affiliation: "Steam4Caster is not affiliated with or endorsed by IsThereAnyDeal or Valve.",
  };
  return (
    <p className="attribution">
      <a className="link" href={a.url} target="_blank" rel="noreferrer">
        {a.text}
      </a>{" "}
      {a.affiliation}
    </p>
  );
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <p className="error" role="alert">
      {children}
    </p>
  );
}

export function Skeleton({ height = "6rem" }: { height?: string }) {
  return <div className="skeleton" style={{ minHeight: height }} aria-hidden="true" />;
}
