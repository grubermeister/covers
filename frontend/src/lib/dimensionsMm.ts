/**
 * Physical size in millimetres for catalog contributions.
 */

const MM_PAIR_RE = /^\d{1,6}(\.\d{1,2})?$/;

/**
 * Parse legacy free-text size or old ``dimensions`` field
 * into width x height mm strings. Returns null if not confidently parseable.
 */
function legacyDimensionTextToWidthHeight(text: string): { width: string; height: string } | null {
  const raw = String(text ?? "").trim();
  if (!raw) return null;
  const noMm = raw.replace(/\bmm\b/gi, " ").replace(/\s+/g, " ").trim();

  const strictPair = noMm.match(
    /^(\d{1,6}(?:\.\d{1,2})?)\s*[xx]\s*(\d{1,6}(?:\.\d{1,2})?)$/i
  );
  if (strictPair) {
    return { width: strictPair[1], height: strictPair[2] };
  }

  // e.g. "34 x 28 " with only digits / separators (no trailing prose)
  const onlyDimChars = /^[\d.\sxx]+$/i.test(noMm);
  if (onlyDimChars) {
    const loose = noMm.match(
      /(\d{1,6}(?:\.\d{1,2})?)\s*[xx]\s*(\d{1,6}(?:\.\d{1,2})?)/i
    );
    if (loose) {
      return { width: loose[1], height: loose[2] };
    }
  }

  if (/^\d{1,6}(?:\.\d{1,2})?$/.test(noMm)) {
    return { width: noMm, height: noMm };
  }

  return null;
}

/** Allow digits and one decimal point; cap integer part at 6 digits, fractional at 2. */
export function sanitizeMmInput(raw: string): string {
  let s = raw.replace(/[^\d.]/g, "");
  const dot = s.indexOf(".");
  if (dot !== -1) {
    s = s.slice(0, dot + 1) + s.slice(dot + 1).replace(/\./g, "");
  }
  const [intPart = "", frac = ""] = s.split(".");
  const i = intPart.slice(0, 6);
  const f = frac.slice(0, 2);
  if (dot !== -1 && f.length === 0 && !s.slice(dot + 1).length) {
    return `${i}.`;
  }
  return f.length ? `${i}.${f}` : i;
}

export function validateMmPair(width: string, height: string): { width?: string; height?: string } {
  const w = width.trim();
  const h = height.trim();
  if (!w && !h) return {};
  if (!w || !h) {
    const msg = "Enter both width and height (mm), or leave both blank.";
    return { width: msg, height: msg };
  }
  if (!MM_PAIR_RE.test(w) || !MM_PAIR_RE.test(h)) {
    const msg = "Use up to 6 digits and up to 2 decimal places (mm).";
    return { width: msg, height: msg };
  }
  const wn = parseFloat(w);
  const hn = parseFloat(h);
  if (!Number.isFinite(wn) || !Number.isFinite(hn) || wn <= 0 || hn <= 0) {
    const msg = "Dimensions must be greater than zero.";
    return { width: msg, height: msg };
  }
  if (wn > 999999.99 || hn > 999999.99) {
    const msg = "Maximum is 999999.99 mm.";
    return { width: msg, height: msg };
  }
  return {};
}

/** Read width/height from submitted_data / API (snake_case or camelCase). */
export function submittedDataToWidthHeightStrings(sd: Record<string, unknown> | undefined | null): {
  width: string;
  height: string;
} {
  if (!sd || typeof sd !== "object") return { width: "", height: "" };
  const w = String(sd.width_mm ?? sd.widthMm ?? "").trim();
  const h = String(sd.height_mm ?? sd.heightMm ?? "").trim();
  if (w || h) return { width: w, height: h };
  const leg = String(sd.dimensions ?? "").trim();
  const fromLeg = legacyDimensionTextToWidthHeight(leg);
  if (fromLeg) return fromLeg;
  return { width: "", height: "" };
}

/** Dashboard / list: human-readable size from submission JSON. */
export function formatSizeFromSubmittedData(sd: Record<string, unknown> | undefined | null): string {
  if (!sd || typeof sd !== "object") return "";
  const w = String(sd.width_mm ?? sd.widthMm ?? "").trim();
  const h = String(sd.height_mm ?? sd.heightMm ?? "").trim();
  if (w && h) return `${w}x${h} mm`;
  const d = String(sd.dimensions ?? "").trim();
  if (!d) return "";
  if (/mm|x|\bcm\b|x/i.test(d)) return d;
  return `${d} mm`;
}

/** From catalog size rows (latest-first consumer provides `first`). */
export function catalogSizeToWidthHeightStrings(firstSize: Record<string, unknown> | undefined | null): {
  width: string;
  height: string;
} {
  if (!firstSize) return { width: "", height: "" };
  const wn = parseFloat(String(firstSize.width ?? ""));
  const hn = parseFloat(String(firstSize.height ?? ""));
  if (Number.isFinite(wn) && wn > 0 && Number.isFinite(hn) && hn > 0) {
    const fmt = (n: number) => {
      if (Number.isInteger(n)) return String(n);
      const t = (Math.round(n * 100) / 100).toFixed(2).replace(/\.?0+$/, "");
      return t || String(n);
    };
    return { width: fmt(wn), height: fmt(hn) };
  }
  const notes = String(firstSize.size_notes ?? firstSize.sizeNotes ?? "").trim();
  const fromNotes = legacyDimensionTextToWidthHeight(notes);
  if (fromNotes) return fromNotes;
  return { width: "", height: "" };
}
