import { Fragment, type ReactNode } from "react";

// A small, dependency-free Markdown renderer for the bounded subset used by the pricing
// write-ups under docs/pricing/* (headings, paragraphs, GFM tables, lists, blockquotes,
// fenced code, rules, and inline bold / italic / code / links). It is intentionally not a
// general-purpose parser — it covers exactly what those docs contain. Source files (src/…)
// and cross-doc (.md) links are rendered as non-navigating references (the methodology is
// reachable via the panel's tabs); only http(s) links become real anchors.

// ---- inline formatting ---------------------------------------------------- //

interface InlinePattern {
  re: RegExp;
  type: "code" | "bold" | "link" | "italic";
}

// Note: emphasis uses '*' only (never '_'), so snake_case identifiers and file paths render
// literally. Bold is matched before italic via earliest-match + length.
const INLINE: InlinePattern[] = [
  { re: /`([^`]+)`/, type: "code" },
  { re: /\*\*([^*]+)\*\*/, type: "bold" },
  { re: /\[([^\]]+)\]\(([^)]+)\)/, type: "link" },
  { re: /\*([^*]+)\*/, type: "italic" },
];

function LinkRef({ text, href }: { text: string; href: string }) {
  if (/^https?:\/\//.test(href)) {
    return (
      <a href={href} target="_blank" rel="noreferrer" className="text-info-fg hover:underline">
        {text}
      </a>
    );
  }
  // relative doc / source-tree reference — show it as a styled reference with the path on hover.
  return (
    <span className="text-fg-secondary underline decoration-dotted decoration-slate-300" title={href}>
      {text}
    </span>
  );
}

function renderInline(text: string, kp: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let rest = text;
  let i = 0;
  while (rest.length) {
    let best: { p: InlinePattern; m: RegExpExecArray } | null = null;
    for (const p of INLINE) {
      const m = p.re.exec(rest);
      if (m && (best === null || m.index < best.m.index)) best = { p, m };
    }
    if (!best) {
      nodes.push(rest);
      break;
    }
    const { p, m } = best;
    if (m.index > 0) nodes.push(rest.slice(0, m.index));
    const key = `${kp}-${i++}`;
    if (p.type === "code") {
      nodes.push(
        <code key={key} className="rounded bg-surface-0 px-1 py-0.5 font-mono text-[0.85em] text-fg">
          {m[1]}
        </code>,
      );
    } else if (p.type === "bold") {
      nodes.push(
        <strong key={key} className="font-semibold text-fg">
          {m[1]}
        </strong>,
      );
    } else if (p.type === "italic") {
      nodes.push(
        <em key={key} className="text-fg-secondary">
          {m[1]}
        </em>,
      );
    } else {
      nodes.push(<LinkRef key={key} text={m[1]} href={m[2]} />);
    }
    rest = rest.slice(m.index + m[0].length);
  }
  return nodes;
}

// ---- block parsing -------------------------------------------------------- //

const HEADING = /^(#{1,6})\s+(.*)$/;
const HR = /^(-{3,}|\*{3,})\s*$/;
const ULI = /^\s*[-*]\s+(.*)$/;
const OLI = /^\s*\d+\.\s+(.*)$/;
const TABLE_ROW = /^\s*\|.*\|\s*$/;
const TABLE_SEP = /^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$/;

function splitCells(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((c) => c.trim());
}

export function Markdown({ source }: { source: string }) {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  let key = 0;
  const k = () => `b${key++}`;

  while (i < lines.length) {
    const line = lines[i];

    // blank
    if (line.trim() === "") {
      i++;
      continue;
    }

    // fenced code
    if (line.trim().startsWith("```")) {
      const buf: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith("```")) {
        buf.push(lines[i]);
        i++;
      }
      i++; // closing fence
      out.push(
        <pre key={k()} className="overflow-x-auto rounded-md bg-surface-code border border-border p-3 text-xs leading-relaxed text-fg-code">
          <code>{buf.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    // horizontal rule
    if (HR.test(line)) {
      out.push(<hr key={k()} className="my-4 border-subtle" />);
      i++;
      continue;
    }

    // heading
    const h = HEADING.exec(line);
    if (h) {
      const level = h[1].length;
      const content = renderInline(h[2], k());
      if (level <= 1) out.push(<h2 key={k()} className="mt-1 text-lg font-semibold text-fg">{content}</h2>);
      else if (level === 2)
        out.push(
          <h3 key={k()} className="mt-5 border-b border-subtle pb-1 text-xs font-semibold uppercase tracking-wide text-fg-muted">
            {content}
          </h3>,
        );
      else out.push(<h4 key={k()} className="mt-4 text-sm font-semibold text-fg">{content}</h4>);
      i++;
      continue;
    }

    // table
    if (TABLE_ROW.test(line) && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1])) {
      const header = splitCells(line);
      i += 2; // header + separator
      const rows: string[][] = [];
      while (i < lines.length && TABLE_ROW.test(lines[i])) {
        rows.push(splitCells(lines[i]));
        i++;
      }
      out.push(
        <div key={k()} className="my-3 overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border text-left text-fg-muted">
                {header.map((c, ci) => (
                  <th key={ci} className="px-2 py-1 font-medium">
                    {renderInline(c, `${key}h${ci}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {rows.map((r, ri) => (
                <tr key={ri} className="align-top">
                  {r.map((c, ci) => (
                    <td key={ci} className="px-2 py-1 text-fg-secondary">
                      {renderInline(c, `${key}r${ri}c${ci}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    // blockquote
    if (line.trimStart().startsWith(">")) {
      const buf: string[] = [];
      while (i < lines.length && lines[i].trimStart().startsWith(">")) {
        buf.push(lines[i].replace(/^\s*>\s?/, ""));
        i++;
      }
      // split into paragraphs on blank lines within the quote
      const paras = buf.join("\n").split(/\n\s*\n/).filter((p) => p.trim() !== "");
      out.push(
        <blockquote key={k()} className="my-3 space-y-2 rounded-md border-l-2 border-strong bg-surface-2 px-3 py-2 text-xs leading-relaxed text-fg-secondary">
          {paras.map((p, pi) => (
            <p key={pi}>{renderInline(p.replace(/\n/g, " "), `${key}q${pi}`)}</p>
          ))}
        </blockquote>,
      );
      continue;
    }

    // unordered list
    if (ULI.test(line)) {
      const items: string[] = [];
      while (i < lines.length && ULI.test(lines[i])) {
        items.push((ULI.exec(lines[i]) as RegExpExecArray)[1]);
        i++;
      }
      out.push(
        <ul key={k()} className="my-2 list-disc space-y-1 pl-5 text-sm leading-relaxed text-fg-secondary">
          {items.map((it, ii) => (
            <li key={ii}>{renderInline(it, `${key}u${ii}`)}</li>
          ))}
        </ul>,
      );
      continue;
    }

    // ordered list
    if (OLI.test(line)) {
      const items: string[] = [];
      while (i < lines.length && OLI.test(lines[i])) {
        items.push((OLI.exec(lines[i]) as RegExpExecArray)[1]);
        i++;
      }
      out.push(
        <ol key={k()} className="my-2 list-decimal space-y-1 pl-5 text-sm leading-relaxed text-fg-secondary">
          {items.map((it, ii) => (
            <li key={ii}>{renderInline(it, `${key}o${ii}`)}</li>
          ))}
        </ol>,
      );
      continue;
    }

    // paragraph: gather consecutive plain lines
    const buf: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !HEADING.test(lines[i]) &&
      !HR.test(lines[i]) &&
      !ULI.test(lines[i]) &&
      !OLI.test(lines[i]) &&
      !lines[i].trimStart().startsWith(">") &&
      !lines[i].trim().startsWith("```") &&
      !(TABLE_ROW.test(lines[i]) && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1]))
    ) {
      buf.push(lines[i]);
      i++;
    }
    out.push(
      <p key={k()} className="my-2 text-sm leading-relaxed text-fg-secondary">
        {renderInline(buf.join(" "), k())}
      </p>,
    );
  }

  return <div className="markdown">{out.map((node, idx) => <Fragment key={idx}>{node}</Fragment>)}</div>;
}
