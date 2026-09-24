"use client";

import React from "react";

interface FormattedMessageProps {
  content: string;
  sender: "USER" | "AGENT" | "SYSTEM";
}

export default function FormattedMessage({ content, sender }: FormattedMessageProps) {
  if (!content) return null;

  const isUser = sender === "USER";

  // Split into lines to preserve structured lists and paragraphs
  const lines = content.split("\n");

  const renderInlineFormatted = (text: string) => {
    // Regex matches:
    // 1. Bold: **text**
    // 2. Inline code: `text`
    // 3. Arrow transitions: 0% → 14%
    // 4. Activity/Location tags: [A-Z]{2,4}-\d{3,5} or F-\d{2,4}
    const parts = text.split(/(\*\*.*?\*\*|`.*?`|\b[A-Z0-9]+-[A-Z0-9]+\b|\d+(?:\.\d+)?%\s*→\s*\d+(?:\.\d+)?%)/g);

    return parts.map((part, idx) => {
      if (!part) return null;

      // Bold **text**
      if (part.startsWith("**") && part.endsWith("**") && part.length >= 4) {
        return (
          <strong key={idx} className={isUser ? "font-semibold text-white" : "font-bold text-slate-900"}>
            {part.slice(2, -2)}
          </strong>
        );
      }

      // Inline code `text`
      if (part.startsWith("`") && part.endsWith("`") && part.length >= 2) {
        return (
          <code
            key={idx}
            className={`px-1.5 py-0.5 rounded font-mono text-xs mx-0.5 ${
              isUser
                ? "bg-blue-700 text-blue-100 border border-blue-500"
                : "bg-slate-100 text-blue-700 border border-slate-200"
            }`}
          >
            {part.slice(1, -1)}
          </code>
        );
      }

      // Percentage transition: 0% → 35%
      if (/\d+(?:\.\d+)?%\s*→\s*\d+(?:\.\d+)?%/.test(part)) {
        return (
          <span
            key={idx}
            className={`inline-flex items-center px-1.5 py-0.5 rounded font-semibold text-xs mx-0.5 ${
              isUser
                ? "bg-emerald-500/30 border border-emerald-300 text-white"
                : "bg-emerald-50 border border-emerald-200 text-emerald-700"
            }`}
          >
            {part}
          </span>
        );
      }

      // Activity Code tag (e.g. CIV-1001, STR-2001, F-204)
      if (/^[A-Z0-9]+-[A-Z0-9]+$/.test(part) && sender === "AGENT") {
        return (
          <span
            key={idx}
            className="inline-flex items-center font-mono font-bold text-blue-700 bg-blue-50 border border-blue-200 px-1.5 py-0.5 rounded text-[11px] mx-0.5"
          >
            {part}
          </span>
        );
      }

      return <span key={idx}>{part}</span>;
    });
  };

  return (
    <div className="space-y-1.5">
      {lines.map((line, lIdx) => {
        const trimmed = line.trim();

        // Empty lines create paragraph breaks
        if (!trimmed) {
          return <div key={lIdx} className="h-1.5" />;
        }

        // Bulleted lists (- item or * item)
        if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
          return (
            <div key={lIdx} className={`flex items-start gap-2 pl-1.5 ${isUser ? "text-white" : "text-slate-800"}`}>
              <span className={`font-bold select-none ${isUser ? "text-blue-200" : "text-blue-600"}`}>•</span>
              <div className="flex-1">{renderInlineFormatted(trimmed.slice(2))}</div>
            </div>
          );
        }

        // Numbered lists (1. item)
        const numMatch = trimmed.match(/^(\d+)\.\s+(.*)$/);
        if (numMatch) {
          return (
            <div key={lIdx} className={`flex items-start gap-2 pl-1.5 ${isUser ? "text-white" : "text-slate-800"}`}>
              <span className={`font-mono text-xs select-none font-semibold ${isUser ? "text-blue-200" : "text-blue-600"}`}>
                {numMatch[1]}.
              </span>
              <div className="flex-1">{renderInlineFormatted(numMatch[2])}</div>
            </div>
          );
        }

        // Section header lines (e.g. "Reported Quantity: +35 m³" or "Current Progress: ...")
        if (
          trimmed.startsWith("Reported Quantity:") ||
          trimmed.startsWith("Current Progress:") ||
          trimmed.includes(" — ")
        ) {
          return (
            <div
              key={lIdx}
              className={`py-0.5 font-medium border-l-2 pl-2.5 my-0.5 ${
                isUser
                  ? "text-white border-blue-300"
                  : "text-slate-900 border-blue-600 bg-slate-50/60 rounded-r"
              }`}
            >
              {renderInlineFormatted(trimmed)}
            </div>
          );
        }

        // Standard line
        return <div key={lIdx}>{renderInlineFormatted(line)}</div>;
      })}
    </div>
  );
}
