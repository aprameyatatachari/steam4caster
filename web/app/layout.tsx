import type { Metadata, Viewport } from "next";
import { Archivo, Martian_Mono } from "next/font/google";
import type { ReactNode } from "react";

import { AuthProvider } from "@/lib/auth";

import "./globals.css";

const archivo = Archivo({
  subsets: ["latin"],
  axes: ["wdth"],
  variable: "--font-archivo",
  display: "swap",
});

const martian = Martian_Mono({
  subsets: ["latin"],
  weight: ["400", "600"],
  variable: "--font-martian",
  display: "swap",
});

export const metadata: Metadata = {
  title: { default: "Steam4Caster — buy now or wait?", template: "%s · Steam4Caster" },
  description:
    "Steam4Caster estimates when a Steam game will next go on sale and how deep, then tells you whether to buy now or wait. Estimates, not guarantees.",
};

export const viewport: Viewport = { themeColor: "#fbfcfe", width: "device-width", initialScale: 1 };

const CONTRACT = `<!--
STEAM4CASTER DIRECTION CONTRACT
THESIS: Prices are steps, so everything is built from cells on one visible grid: letterforms, charts and layout share an armature. Refuses the dark navy price-tracker dashboard with a smooth line chart.
OWN-WORLD: Cool graph paper with blue construction hairlines; ink black; blue pen for line work and one blueprint field; a single highlighter yellow used only as filled planes. Square cells, one corner cut on the diagonal. Extended uppercase grotesk over cell-built display letters.
STORY: A price does not drift, it steps; past steps repeat enough to estimate the next; you get a call with its reasons, and the score is kept. Start a watchlist.
FIRST VIEWPORT: BUY NOW / OR WAIT? assembled cell by cell across 42 of 48 columns, WAIT? in pen blue; lede and two actions below left; an estimated-next-seasonal-sale readout below right. The pointer highlights cells.
FORM: Construction Grid (catalog challenger, chosen by the owner over the rolled direction); seed key ac0a2bcd.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, and DESIGN.md
-->`;

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${archivo.variable} ${martian.variable}`}>
      <body>
        <div hidden dangerouslySetInnerHTML={{ __html: CONTRACT }} />
        <AuthProvider>
          <div className="sheet">
            <div className="sheet-inner gridded">{children}</div>
          </div>
        </AuthProvider>
      </body>
    </html>
  );
}
