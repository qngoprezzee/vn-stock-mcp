"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutDashboard, TrendingUp, Calculator, Activity, FileText, Sun, Scale } from "lucide-react";

const nav = [
  { href: "/",              label: "Dashboard",      icon: LayoutDashboard },
  { href: "/brief",         label: "Morning Brief",  icon: Sun },
  { href: "/screener",      label: "Quality Screen", icon: TrendingUp },
  { href: "/position-sizer",label: "Position Sizer", icon: Calculator },
  { href: "/thesis",        label: "New Thesis",     icon: FileText },
  { href: "/compare",       label: "Compare",        icon: Scale },
  { href: "/performance",   label: "Performance",    icon: Activity },
];

export function NavBar() {
  const path = usePathname();
  return (
    <nav className="border-b border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-950 sticky top-0 z-10">
      <div className="max-w-6xl mx-auto px-6 py-3 flex items-center gap-2">
        <Link href="/" className="font-semibold text-slate-900 dark:text-slate-100 mr-6">
          🇻🇳 VN Stock
        </Link>
        <div className="flex gap-1 flex-1">
          {nav.map(({ href, label, icon: Icon }) => {
            const active = path === href;
            return (
              <Link
                key={href}
                href={href}
                className={`flex items-center gap-2 px-3 py-1.5 rounded-md text-sm transition-colors ${
                  active
                    ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900"
                    : "text-slate-600 hover:text-slate-900 hover:bg-slate-100 dark:text-slate-400 dark:hover:text-slate-100 dark:hover:bg-slate-800"
                }`}
              >
                <Icon size={16} />
                {label}
              </Link>
            );
          })}
        </div>
      </div>
    </nav>
  );
}
