"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const tabs = [
  { href: "/record", label: "Record", base: "border-sky-300 bg-sky-100 text-sky-900 hover:bg-sky-200 dark:border-sky-700 dark:bg-sky-950 dark:text-sky-100 dark:hover:bg-sky-900", active: "border-sky-300 bg-sky-50 text-sky-950 dark:border-sky-500 dark:bg-sky-900 dark:text-white" },
  { href: "/upload", label: "Upload", base: "border-emerald-300 bg-emerald-100 text-emerald-900 hover:bg-emerald-200 dark:border-emerald-700 dark:bg-emerald-950 dark:text-emerald-100 dark:hover:bg-emerald-900", active: "border-emerald-300 bg-emerald-50 text-emerald-950 dark:border-emerald-500 dark:bg-emerald-900 dark:text-white" },
  { href: "/progress", label: "Progress", base: "border-violet-300 bg-violet-100 text-violet-900 hover:bg-violet-200 dark:border-violet-700 dark:bg-violet-950 dark:text-violet-100 dark:hover:bg-violet-900", active: "border-violet-300 bg-violet-50 text-violet-950 dark:border-violet-500 dark:bg-violet-900 dark:text-white" },
];

export function MainNav() {
  const pathname = usePathname();

  return (
    <nav className="mx-auto flex max-w-6xl items-end gap-1 px-4 pt-3" aria-label="Main navigation">
      {tabs.map((tab) => {
        const current = pathname === tab.href;
        return (
          <Link
            key={tab.href}
            href={tab.href}
            aria-current={current ? "page" : undefined}
            className={`min-h-11 rounded-t-xl border border-b-0 px-4 py-2 text-sm font-semibold shadow-sm focus-visible:z-10 focus-visible:outline-2 focus-visible:outline-offset-2 ${current ? tab.active : tab.base}`}
          >
            {tab.label}
          </Link>
        );
      })}
      <Link href="/record" className="ml-3 pb-2 text-sm font-semibold text-slate-700 dark:text-slate-200">
        Dive Form Analyzer
      </Link>
    </nav>
  );
}
