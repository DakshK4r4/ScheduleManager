"use client";

import React, { useState, useEffect } from "react";
import Link from "next/link";
import { Layers, ShieldCheck, Search } from "lucide-react";
import AttentionCenter from "./ui/AttentionCenter";
import CommandPalette from "./ui/CommandPalette";

export default function Navbar() {
  const [isCommandPaletteOpen, setIsCommandPaletteOpen] = useState(false);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setIsCommandPaletteOpen((prev) => !prev);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  return (
    <>
      <header className="sticky top-0 z-40 w-full border-b border-slate-200/80 bg-white/95 backdrop-blur-md">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8 gap-4">
          {/* Logo & Platform Title */}
          <Link href="/" className="flex items-center gap-3 group shrink-0">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-600 text-white shadow-xs transition-transform group-hover:scale-105">
              <Layers className="h-5 w-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="font-bold text-slate-900 text-base sm:text-lg tracking-tight">
                  Primavera Platform
                </span>
                <span className="rounded bg-blue-50 px-1.5 py-0.5 text-[10px] font-bold text-blue-700 border border-blue-200/60 uppercase tracking-wider">
                  DEMO
                </span>
              </div>
              <p className="text-xs text-slate-500 font-medium">Schedule Viewer &amp; Editor</p>
            </div>
          </Link>

          {/* Center: Command Palette Trigger */}
          <div className="flex-1 max-w-md hidden md:block">
            <button
              onClick={() => setIsCommandPaletteOpen(true)}
              className="w-full flex items-center justify-between px-3 py-1.5 text-xs text-slate-400 bg-slate-50 hover:bg-slate-100/80 border border-slate-200 rounded-lg transition-colors group"
            >
              <div className="flex items-center gap-2">
                <Search className="h-3.5 w-3.5 text-slate-400 group-hover:text-slate-600" />
                <span className="group-hover:text-slate-700">Search projects, activities, or commands...</span>
              </div>
              <kbd className="hidden sm:inline-block rounded border border-slate-200 bg-white px-1.5 py-0.5 text-[10px] font-mono text-slate-500 shadow-2xs">
                ⌘K
              </kbd>
            </button>
          </div>

          {/* Right: Actions, Attention Center, DB Status */}
          <nav className="flex items-center gap-3 sm:gap-4 shrink-0">
            {/* Mobile search button */}
            <button
              onClick={() => setIsCommandPaletteOpen(true)}
              className="md:hidden flex h-9 w-9 items-center justify-center rounded-md border border-slate-200 text-slate-500 hover:bg-slate-50"
              title="Search (⌘K)"
            >
              <Search className="h-4 w-4" />
            </button>

            {/* Global Attention Center */}
            <AttentionCenter />

            {/* PostgreSQL Status Indicator */}
            <div className="flex items-center gap-1.5 rounded-full border border-emerald-200/80 bg-emerald-50 px-2.5 py-1 text-[11px] font-medium text-emerald-800">
              <span className="h-2 w-2 rounded-full bg-emerald-500" />
              <span className="hidden sm:inline">PostgreSQL</span>
            </div>
          </nav>
        </div>
      </header>

      {/* Global Command Palette Modal */}
      <CommandPalette
        isOpen={isCommandPaletteOpen}
        onClose={() => setIsCommandPaletteOpen(false)}
      />
    </>
  );
}
