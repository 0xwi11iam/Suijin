import { useState } from "react";

/** A provider icon with a guaranteed monogram. The cloud image is shown only
 * after it actually decodes; until then (or if it fails) the initials are the
 * icon, so a missing/slow/blank CDN image never leaves an empty square. */
export function ProviderGlyph({ src, label, className }: { src?: string; label?: string; className?: string }) {
  const words = String(label || "?").split(/\s+/).filter(Boolean);
  const monogram = (words.length > 1
    ? words.slice(0, 2).map((word) => word[0]).join("")
    : (words[0] ?? "?").slice(0, 2)).toUpperCase();
  const [state, setState] = useState<"pending" | "ready" | "failed">(src ? "pending" : "failed");
  return (
    <span aria-hidden="true" className={`relative inline-flex size-4 shrink-0 items-center justify-center overflow-hidden rounded-[3px] bg-muted text-[8px] font-semibold leading-none text-foreground ${className ?? ""}`}>
      {state !== "ready" && <span className="absolute inset-0 grid place-items-center">{monogram}</span>}
      {src && state !== "failed" ? <img
        src={src}
        alt=""
        className="relative size-full rounded-[3px] object-contain"
        loading="lazy"
        onLoad={(event) => {
          // A valid PNG can still be a blank/dark square on this dark UI.
          // Sample it; if it has no visible bright ink, keep the monogram.
          try {
            const image = event.currentTarget;
            const canvas = document.createElement("canvas");
            canvas.width = 16;
            canvas.height = 16;
            const context = canvas.getContext("2d");
            if (!context) { setState("ready"); return; }
            context.drawImage(image, 0, 0, 16, 16);
            const data = context.getImageData(0, 0, 16, 16).data;
            let opaque = 0;
            let bright = 0;
            for (let index = 0; index < data.length; index += 4) {
              if (data[index + 3] < 32) continue;
              opaque += 1;
              if (data[index] + data[index + 1] + data[index + 2] > 180) bright += 1;
            }
            setState(opaque > 0 && bright >= 3 ? "ready" : "failed");
          } catch {
            setState("ready");
          }
        }}
        onError={() => setState("failed")}
      /> : null}
    </span>
  );
}
