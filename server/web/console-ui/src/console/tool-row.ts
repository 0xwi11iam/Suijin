import type { ToolCallState } from "./transcript";

/** Operator ruling, 2026-11-28 (after the mockups): a tool row is white Inter text that names
 * the tool — "Used CRT.sh", "Executed Terminal". Opening it shows the coloured JSON it ran.
 * Exactly two exceptions: a CONFIRMED exploit earns a simple box, and read/wrote file's dropdown
 * shows the path of the file. Nothing else is special. */

export type ToolLabels = Record<string, string>;

function short(name: string): string {
  return name.replace(/^.*[.:/]/, "").toLowerCase();
}

/** Pack tools carry an action suffix (nmap_scan, gobuster_dir) — the row names the TOOL
 *  (official-lowercase styling: nmap, sqlmap, nikto — operator convention). */
const TOOL_SUFFIXES = new Set(["scan", "probe", "search", "check", "dir", "dns", "brute", "recon", "hunt", "grab", "fetch", "enum", "audit", "info", "lookup"]);

function plain(name: string): string {
  const parts = short(name).split("_");
  if (parts.length > 1 && TOOL_SUFFIXES.has(parts.at(-1)!)) return parts.slice(0, -1).join("_");
  return short(name);
}

/** The one row line: verb + name, white, no colour, no icons beyond the one glyph. */
export function toolRowTitle(call: Pick<ToolCallState, "name" | "arguments" | "result"> & { isRunning?: boolean; status?: string }, labels: ToolLabels): string {
  const name = short(call.name);
  if (call.isRunning) return `Running ${labels[name] ?? name}`;
  const status = call.status ?? "";
  if (status === "Error") return `Failed ${labels[name] ?? name}`;
  if (status === "Stopped") return `Stopped ${labels[name] ?? name}`;
  if (/^(execute_terminal|bash|shell|terminal|run_command|exec_command)$/.test(name)) return "Executed Terminal";
  if (/^(read_file|read|file_read)$/.test(name)) return "Read file";
  if (/^(write_file|file_write)$/.test(name)) return "Wrote file";
  if (name === "catalog_exploit") return "Catalogued exploit";
  return `Used ${labels[name] ?? plain(name)}`;
}

/** The file tools' dropdown: the path, nothing else. */
export function filePathFromCall(call: Pick<ToolCallState, "name" | "arguments" | "result">): string | undefined {
  if (!/^(read_file|read|file_read|write_file|file_write)$/.test(short(call.name))) return undefined;
  try {
    const raw = call.arguments;
    const args = JSON.parse(typeof raw === "string" && raw.trim() ? raw : "{}") as Record<string, unknown>;
    const path = args.path ?? args.file_path ?? args.filename;
    return typeof path === "string" && path.trim() ? path.trim() : undefined;
  } catch {
    return undefined;
  }
}

export interface ExploitBox { expId: string; severity: string; cvss: string; title: string; target?: string }

/** The cataloguer's verdict line, parsed — only a CONFIRMED verdict gets the box. */
export function exploitBoxFromResult(result: unknown): ExploitBox | undefined {
  const text = typeof result === "string" ? result : result === undefined || result === null ? "" : JSON.stringify(result);
  const m = /((?:EXP|exp)[-_\s]?\d+)\s+CONFIRMED\s*[—–-]\s*(CRITICAL|HIGH|MEDIUM|LOW|INFO(?:RMATIONAL)?)\s+CVSS\s+([\d.]+)\s*:\s*(.+?)(?:\s*\(target\s+([^)]+)\))?(?:[.\n]|$)/i.exec(text);
  if (!m) return undefined;
  return { expId: m[1]!.toUpperCase().replace(/[\s_]/g, "-"), severity: m[2]!.toUpperCase(), cvss: m[3]!, title: m[4]!.trim(), target: m[5]?.trim() };
}
