/**
 * Guess whether an image shows a whole cover or a closeup of a marking, from
 * its pixel dimensions alone (issue #76).
 *
 * Contributors reach the marking form from the prominent "Submit New Marking"
 * button, so someone holding a cover photograph tends to upload it there. The
 * check below catches the common case cheaply -- no model, no network call, no
 * API key on the server, nothing to be unavailable at submit time.
 *
 * Thresholds come from measuring both live sites on 2026-08-05: correctly
 * slotted marking closeups cluster at 0.01-0.05 MP and are roughly square,
 * while cover scans cluster at 1.0-1.75 MP and are landscape. Validated against
 * the only labelled set available (v1's txtView): flags 40 of 44 known cover
 * views, i.e. ~91% recall.
 *
 * This is a nudge, never a gate. Precision is not measurable from that labelled
 * set, and legitimate marking aspect ratios reach 8.6 (wide straight-line
 * handstamps), so every caller must offer an acknowledge-and-continue path.
 */

export interface ImageDimensions {
  width: number;
  height: number;
}

export type ImageShape = "cover-like" | "marking-like" | "indeterminate";

/** Landscape enough to read as a cover rather than a marking closeup. */
export const COVER_MIN_ASPECT = 1.25;
/** Cover scans are big; marking closeups are cropped small. 0.6 megapixels. */
export const COVER_MIN_PIXELS = 600_000;
/**
 * Below this a landscape image is too small to be a cover scan -- it is a
 * cropped marking that simply happens to be wide. 0.3 megapixels.
 */
export const MARKING_MAX_PIXELS = 300_000;
/** Wider or taller than this and "square-ish" no longer describes it. */
export const MARKING_MAX_ASPECT = 1.25;

/**
 * Classify an image by shape.
 *
 * Returns "indeterminate" for anything that is not clearly one or the other --
 * the middle band is genuinely ambiguous and warning on it would train
 * contributors to dismiss the warning.
 */
export function classifyImageShape({ width, height }: ImageDimensions): ImageShape {
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    return "indeterminate";
  }
  const aspect = width / height;
  const pixels = width * height;
  if (aspect >= COVER_MIN_ASPECT && pixels >= COVER_MIN_PIXELS) {
    return "cover-like";
  }
  if (aspect < MARKING_MAX_ASPECT && pixels <= MARKING_MAX_PIXELS) {
    return "marking-like";
  }
  return "indeterminate";
}

/**
 * True when this image looks like the wrong kind for the form it is on.
 *
 * `expected` is what the form collects, so the marking form warns on
 * "cover-like" and the cover form warns on "marking-like".
 */
export function looksLikeWrongKind(
  dimensions: ImageDimensions,
  expected: "MARKING" | "COVER",
): boolean {
  const shape = classifyImageShape(dimensions);
  return expected === "MARKING" ? shape === "cover-like" : shape === "marking-like";
}

/**
 * Read a selected file's natural dimensions in the browser.
 *
 * Resolves to null rather than rejecting when the image cannot be decoded: a
 * failed measurement must never block an upload the server would have accepted
 * (TIFF, for one, is an allowed upload type that most browsers cannot render).
 */
export function measureImageFile(file: Blob): Promise<ImageDimensions | null> {
  return new Promise((resolve) => {
    if (typeof URL === "undefined" || typeof URL.createObjectURL !== "function") {
      resolve(null);
      return;
    }
    const url = URL.createObjectURL(file);
    const img = new Image();
    const done = (value: ImageDimensions | null) => {
      URL.revokeObjectURL(url);
      resolve(value);
    };
    img.onload = () => done({ width: img.naturalWidth, height: img.naturalHeight });
    img.onerror = () => done(null);
    img.src = url;
  });
}
