import { useEffect, useMemo, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useToast } from "@/hooks/use-toast";
import { getColors, type ColorOption } from "@/services/colors";
import { getDirectCatalogCodeSuggestion } from "@/services/contributions";
import {
  createCover,
  createCoverMarking,
  createDateSeen,
  deleteDateSeen,
  updateCover,
  updateCoverMarking,
  updateDateSeen,
  type DateSeenGranularity,
  type CoverWritePayload,
} from "@/services/covers";
import type { AssociatedCover, AssociatedDateSeen } from "@/services/markings";

/**
 * CoverDialog
 * -----------
 * Single modal that powers both "Submit New Cover" and "Edit Cover" flows
 * on the Record Detail page. The Cover graph spans three resources on the
 * backend (Cover, CoverMarking, DateSeen) and the dialog is the only
 * place in the SPA where editors/contributors get to edit all three at
 * once, so the orchestration lives here rather than being duplicated by
 * each caller.
 *
 * DateSeen is polymorphic (subject_type=COVER|MARKING + subject_id);
 * this dialog only ever writes COVER-scoped rows for the cover it is
 * editing.
 *
 * Save flow:
 *   create mode -> POST /covers/         (Cover row)
 *               -> POST /cover-markings/ (link to current marking)
 *               -> POST /dates-seen/     (one per row, subject_type=COVER,
 *                                         subject_id=<new cover.id>)
 *   edit   mode -> PATCH /covers/{id}/
 *               -> PATCH /cover-markings/{id}/
 *               -> diff dates: PATCH/POST/DELETE per row vs. the original.
 *
 * onSaved is invoked after a successful save so the parent can refetch
 * its associated-covers list.
 */

type Mode = "create" | "edit";

type FormDate = {
  /** Existing DateSeen.id (edit mode only); undefined when locally added. */
  existingId?: number;
  /** ISO YYYY-MM-DD value bound to <input type="date">. */
  date: string;
  granularity: DateSeenGranularity;
};

interface CoverDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mode: Mode;
  /** Marking PK the cover is being attached to / was attached to. */
  markingId: number;
  /** Required in edit mode; ignored in create mode. */
  cover?: AssociatedCover | null;
  /**
   * Called after a successful create/update so the parent can refresh its
   * cached cover list. May be sync or async; if it returns a Promise the
   * dialog awaits it before closing so the next open shows fresh data.
   */
  onSaved?: () => void | Promise<void>;
}

const COVER_TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: "FC", label: "FC - Folded Cover" },
  { value: "FL", label: "FL - Folded Letter" },
];

const GRANULARITY_OPTIONS: { value: DateSeenGranularity; label: string }[] = [
  { value: "DAY", label: "Day (MM/DD/YYYY)" },
  { value: "MONTH", label: "Month (MM/YYYY)" },
  { value: "YEAR", label: "Year (YYYY)" },
];

const NO_COLOR_VALUE = "__none__";
const NO_TYPE_VALUE = "__none__";

/**
 * Pad an ISO date string up to a full YYYY-MM-DD, defaulting month/day to
 * "01" so a YEAR or MONTH granularity still parses cleanly when stored
 * back into <input type="date">. The Cover form lets the user enter just
 * a year for YEAR-granularity dates, and we don't want to throw away the
 * day they typed for DAY-granularity covers.
 */
function normalizeFormDate(
  raw: string,
  granularity: DateSeenGranularity,
): string {
  const trimmed = (raw ?? "").trim();
  if (!trimmed) return "";
  if (granularity === "YEAR") {
    const yearMatch = /^(\d{4})/.exec(trimmed);
    return yearMatch ? `${yearMatch[1]}-01-01` : trimmed;
  }
  if (granularity === "MONTH") {
    const monthMatch = /^(\d{4})-(\d{2})/.exec(trimmed);
    return monthMatch ? `${monthMatch[1]}-${monthMatch[2]}-01` : trimmed;
  }
  return trimmed;
}

/** Convert AssociatedDateSeen (read model) -> FormDate (write model). */
function toFormDate(d: AssociatedDateSeen): FormDate {
  return {
    existingId: d.id,
    date: d.date ? d.date.slice(0, 10) : "",
    granularity:
      d.granularity === "YEAR" || d.granularity === "MONTH" || d.granularity === "DAY"
        ? d.granularity
        : "DAY",
  };
}

/**
 * Build initial form state from an existing AssociatedCover (edit mode)
 * or empty defaults (create mode). Memoised in the component so that
 * cancelling and re-opening the dialog presents a freshly-prefilled form
 * rather than the user's mid-edit state.
 */
function buildInitialState(cover: AssociatedCover | null | undefined) {
  const c = cover?.coverDetails ?? null;
  return {
    code: c?.code ?? "",
    colorId:
      c?.colorId != null && Number.isFinite(c.colorId) ? String(c.colorId) : "",
    type: c?.type ?? "",
    width: c?.width ?? "",
    height: c?.height ?? "",
    hasAdhesive: c?.hasAdhesive === true,
    isInstitutional: c?.isInstitutional === true,
    isBackstamp: cover?.isBackstamp === true,
    placement: cover?.placement ?? "",
    dates: c?.datesSeen?.map(toFormDate) ?? [],
  };
}

export function CoverDialog({
  open,
  onOpenChange,
  mode,
  markingId,
  cover,
  onSaved,
}: CoverDialogProps) {
  const { toast } = useToast();
  const initial = useMemo(() => buildInitialState(cover), [cover]);

  const [code, setCode] = useState(initial.code);
  const [colorId, setColorId] = useState(initial.colorId);
  const [type, setType] = useState(initial.type);
  const [width, setWidth] = useState(initial.width);
  const [height, setHeight] = useState(initial.height);
  const [hasAdhesive, setHasAdhesive] = useState(initial.hasAdhesive);
  const [isInstitutional, setIsInstitutional] = useState(
    initial.isInstitutional,
  );
  const [isBackstamp, setIsBackstamp] = useState(initial.isBackstamp);
  const [placement, setPlacement] = useState(initial.placement);
  const [dates, setDates] = useState<FormDate[]>(initial.dates);

  const [colorOptions, setColorOptions] = useState<ColorOption[]>([]);
  const [colorsLoading, setColorsLoading] = useState(false);
  const [catalogCodeLoading, setCatalogCodeLoading] = useState(false);
  const [catalogCodeError, setCatalogCodeError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Re-prime the form whenever the dialog opens, so that switching from
  // "edit cover A" to "edit cover B" -- or to "create new" -- without an
  // unmount in between always shows the right starting values. The reset
  // on close path is important too: without it, partial edits that the
  // user abandons stay in component state and reappear if they reopen
  // the dialog on a *different* row.
  useEffect(() => {
    setCode(initial.code);
    setCatalogCodeError(null);
    setColorId(initial.colorId);
    setType(initial.type);
    setWidth(initial.width);
    setHeight(initial.height);
    setHasAdhesive(initial.hasAdhesive);
    setIsInstitutional(initial.isInstitutional);
    setIsBackstamp(initial.isBackstamp);
    setPlacement(initial.placement);
    setDates(initial.dates);
  }, [open, initial]);

  useEffect(() => {
    if (!open) return;
    if (initial.code.trim()) return;
    let cancelled = false;
    setCatalogCodeLoading(true);
    setCatalogCodeError(null);
    getDirectCatalogCodeSuggestion({
      subjectType: "COVER",
      markingId,
      excludeId: cover?.coverDetails?.id ?? null,
    })
      .then((suggestion) => {
        if (!cancelled) setCode(suggestion.catalogCode);
      })
      .catch((err) => {
        if (!cancelled) {
          setCatalogCodeError(err instanceof Error ? err.message : "Could not generate catalog code.");
        }
      })
      .finally(() => {
        if (!cancelled) setCatalogCodeLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, initial.code, markingId, cover?.coverDetails?.id]);

  // Lazy-load colour options the first time the dialog opens. We don't
  // want to fire this from the parent component because the Record Detail
  // page can render dozens of marking rows server-side; pulling the
  // colour list there would be wasteful when the user never opens the
  // dialog.
  useEffect(() => {
    if (!open || colorOptions.length > 0 || colorsLoading) return;
    let cancelled = false;
    setColorsLoading(true);
    getColors()
      .then((rows) => {
        if (!cancelled) setColorOptions(rows);
      })
      .catch(() => {
        if (!cancelled) setColorOptions([]);
      })
      .finally(() => {
        if (!cancelled) setColorsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, colorOptions.length, colorsLoading]);

  const addDate = () => {
    setDates((prev) => [
      ...prev,
      { date: "", granularity: "DAY" satisfies DateSeenGranularity },
    ]);
  };

  const removeDate = (index: number) => {
    setDates((prev) => prev.filter((_, i) => i !== index));
  };

  const updateDateAt = (
    index: number,
    patch: Partial<Pick<FormDate, "date" | "granularity">>,
  ) => {
    setDates((prev) =>
      prev.map((row, i) => (i === index ? { ...row, ...patch } : row)),
    );
  };

  const close = () => {
    if (submitting) return;
    onOpenChange(false);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting) return;

    // Validate dates: any row with a typed date must parse, and the
    // granularity-specific minimum must be present (year for YEAR,
    // year+month for MONTH, full date for DAY).
    for (const row of dates) {
      const trimmed = (row.date ?? "").trim();
      if (!trimmed) {
        toast({
          title: "Missing cover date",
          description:
            "Each cover-date row needs a date, or remove the row.",
          variant: "destructive",
        });
        return;
      }
      if (row.granularity === "YEAR" && !/^\d{4}/.test(trimmed)) {
        toast({
          title: "Invalid year",
          description: "Year-granularity dates must start with a 4-digit year.",
          variant: "destructive",
        });
        return;
      }
      if (row.granularity === "MONTH" && !/^\d{4}-\d{2}/.test(trimmed)) {
        toast({
          title: "Invalid month",
          description:
            "Month-granularity dates must include both year and month.",
          variant: "destructive",
        });
        return;
      }
    }

    const widthTrim = (width ?? "").trim();
    const heightTrim = (height ?? "").trim();
    let codeTrim = (code ?? "").trim();
    const placementTrim = (placement ?? "").trim();
    if (!codeTrim) {
      setCatalogCodeLoading(true);
      setCatalogCodeError(null);
      try {
        const suggestion = await getDirectCatalogCodeSuggestion({
          subjectType: "COVER",
          markingId,
          excludeId: cover?.coverDetails?.id ?? null,
        });
        codeTrim = suggestion.catalogCode;
        setCode(suggestion.catalogCode);
      } catch (err) {
        setCatalogCodeError(err instanceof Error ? err.message : "Could not generate catalog code.");
        setCatalogCodeLoading(false);
        return;
      }
      setCatalogCodeLoading(false);
    }

    const coverPayload: CoverWritePayload = {
      code: codeTrim || null,
      color: colorId ? Number(colorId) : null,
      type: type || null,
      has_adhesive: hasAdhesive,
      is_institutional: isInstitutional,
      width: widthTrim || null,
      height: heightTrim || null,
    };

    setSubmitting(true);
    try {
      let coverId: number;
      let coverMarkingId: number;
      let originalDates: AssociatedDateSeen[] = [];

      if (mode === "create") {
        const created = await createCover(coverPayload);
        coverId = created.id;
        const link = await createCoverMarking({
          cover: coverId,
          marking: markingId,
          is_backstamp: isBackstamp,
          placement: placementTrim || null,
        });
        coverMarkingId = link.id;
      } else {
        if (!cover || !cover.coverDetails) {
          throw new Error("Cannot edit cover: missing cover details.");
        }
        coverId = cover.coverDetails.id;
        coverMarkingId = cover.id;
        originalDates = cover.coverDetails.datesSeen;

        await updateCover(coverId, coverPayload);
        await updateCoverMarking(coverMarkingId, {
          is_backstamp: isBackstamp,
          placement: placementTrim || null,
        });
      }

      // Reconcile DateSeen rows on this cover: anything still in `dates` is
      // kept (PATCHed if it has an existingId, POSTed otherwise); anything
      // that used to be on the cover but was removed from the form is
      // DELETEd. All writes here are subject_type=COVER, subject_id=coverId.
      const keptIds = new Set(
        dates.map((d) => d.existingId).filter((x): x is number => x != null),
      );
      const deletions = originalDates
        .filter((d) => !keptIds.has(d.id))
        .map((d) => deleteDateSeen(d.id));

      const writes = dates.map((row) => {
        const dateValue = normalizeFormDate(row.date, row.granularity);
        if (row.existingId != null) {
          return updateDateSeen(row.existingId, {
            date: dateValue,
            granularity: row.granularity,
          });
        }
        return createDateSeen({
          cover: coverId,
          date: dateValue,
          granularity: row.granularity,
        });
      });

      await Promise.all([...deletions, ...writes]);

      toast({
        title: mode === "create" ? "Cover added" : "Cover updated",
        description:
          mode === "create"
            ? "The new cover is now linked to this marking."
            : "Your changes have been saved.",
      });
      // Await onSaved so any parent-side refresh (e.g. refetching the
      // associated covers list) completes BEFORE the dialog closes. Without
      // this, reopening edit too quickly can show stale prefilled values
      // because `cover` prop is still the pre-save reference.
      try {
        await onSaved?.();
      } catch {
        // onSaved is a refresh hook, not the primary write path; if it
        // fails we still consider the save successful and just close.
      }
      onOpenChange(false);
    } catch (err) {
      toast({
        title: mode === "create" ? "Could not add cover" : "Could not save cover",
        description: err instanceof Error ? err.message : "Please try again.",
        variant: "destructive",
      });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? onOpenChange(true) : close())}>
      <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {mode === "create" ? "Submit New Cover" : "Edit Cover"}
          </DialogTitle>
          <DialogDescription>
            {mode === "create"
              ? "Describe the cover and the dates it was used. The new cover will be linked to this marking."
              : "Update this cover's details, its link to this marking, and its observed dates."}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4 pt-2">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="cover-code">Catalog key</Label>
              <Input
                id="cover-code"
                value={code}
                onChange={(e) => {
                  setCode(e.target.value);
                  if (catalogCodeError) setCatalogCodeError(null);
                }}
                onBlur={() => {
                  if (!code.trim()) {
                    void getDirectCatalogCodeSuggestion({
                      subjectType: "COVER",
                      markingId,
                      excludeId: cover?.coverDetails?.id ?? null,
                    })
                      .then((suggestion) => setCode(suggestion.catalogCode))
                      .catch((err) => {
                        setCatalogCodeError(
                          err instanceof Error ? err.message : "Could not generate catalog code.",
                        );
                      });
                  }
                }}
                placeholder={catalogCodeLoading ? "Generating..." : "e.g. APMC-VA-C0001"}
                disabled={submitting || catalogCodeLoading}
              />
              {catalogCodeError ? (
                <p className="text-sm text-destructive">{catalogCodeError}</p>
              ) : null}
            </div>

            <div className="space-y-2">
              <Label htmlFor="cover-type">Type</Label>
              <Select
                value={type === "" ? NO_TYPE_VALUE : type}
                onValueChange={(v) => setType(v === NO_TYPE_VALUE ? "" : v)}
                disabled={submitting}
              >
                <SelectTrigger id="cover-type">
                  <SelectValue placeholder="Choose a cover type" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NO_TYPE_VALUE}>(none)</SelectItem>
                  {COVER_TYPE_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label htmlFor="cover-color">Color</Label>
              <Select
                value={colorId === "" ? NO_COLOR_VALUE : colorId}
                onValueChange={(v) =>
                  setColorId(v === NO_COLOR_VALUE ? "" : v)
                }
                disabled={submitting || colorsLoading}
              >
                <SelectTrigger id="cover-color">
                  <SelectValue
                    placeholder={
                      colorsLoading ? "Loading colors..." : "Choose a color"
                    }
                  />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NO_COLOR_VALUE}>(none)</SelectItem>
                  {colorOptions.map((opt) => (
                    <SelectItem key={opt.id} value={String(opt.id)}>
                      {opt.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label htmlFor="cover-placement">Placement</Label>
              <Input
                id="cover-placement"
                value={placement}
                onChange={(e) => setPlacement(e.target.value)}
                placeholder="e.g. upper-right"
                disabled={submitting}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="cover-width">Width (mm)</Label>
              <Input
                id="cover-width"
                type="number"
                inputMode="decimal"
                step="0.01"
                min="0"
                value={width}
                onChange={(e) => setWidth(e.target.value)}
                disabled={submitting}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="cover-height">Height (mm)</Label>
              <Input
                id="cover-height"
                type="number"
                inputMode="decimal"
                step="0.01"
                min="0"
                value={height}
                onChange={(e) => setHeight(e.target.value)}
                disabled={submitting}
              />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-1">
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={hasAdhesive}
                onCheckedChange={(v) => setHasAdhesive(v === true)}
                disabled={submitting}
              />
              Has adhesive
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={isInstitutional}
                onCheckedChange={(v) => setIsInstitutional(v === true)}
                disabled={submitting}
              />
              Institutionally Owned
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={isBackstamp}
                onCheckedChange={(v) => setIsBackstamp(v === true)}
                disabled={submitting}
              />
              Backstamp
            </label>
          </div>

          <div className="space-y-2 pt-2">
            <div className="flex items-center justify-between">
              <Label>Cover dates</Label>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={addDate}
                disabled={submitting}
              >
                <Plus className="mr-2 h-4 w-4" />
                Add date
              </Button>
            </div>
            {dates.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No dates added yet. Use "Add date" to record at least one observed use.
              </p>
            ) : (
              <div className="space-y-2">
                {dates.map((row, idx) => (
                  <div
                    key={row.existingId ?? `new-${idx}`}
                    className="grid grid-cols-1 sm:grid-cols-[1fr_180px_auto] gap-2 items-end"
                  >
                    <div className="space-y-1">
                      <Label
                        htmlFor={`cover-date-${idx}`}
                        className="text-xs text-muted-foreground"
                      >
                        Date
                      </Label>
                      <Input
                        id={`cover-date-${idx}`}
                        type={row.granularity === "YEAR" ? "text" : "date"}
                        value={row.date}
                        onChange={(e) =>
                          updateDateAt(idx, { date: e.target.value })
                        }
                        placeholder={
                          row.granularity === "YEAR" ? "YYYY" : undefined
                        }
                        disabled={submitting}
                      />
                    </div>
                    <div className="space-y-1">
                      <Label
                        htmlFor={`cover-granularity-${idx}`}
                        className="text-xs text-muted-foreground"
                      >
                        Granularity
                      </Label>
                      <Select
                        value={row.granularity}
                        onValueChange={(v) =>
                          updateDateAt(idx, {
                            granularity: v as DateSeenGranularity,
                          })
                        }
                        disabled={submitting}
                      >
                        <SelectTrigger id={`cover-granularity-${idx}`}>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {GRANULARITY_OPTIONS.map((opt) => (
                            <SelectItem key={opt.value} value={opt.value}>
                              {opt.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      onClick={() => removeDate(idx)}
                      disabled={submitting}
                      aria-label="Remove date"
                    >
                      <Trash2 className="h-4 w-4 text-destructive" />
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="flex justify-end gap-3 pt-4">
            <Button
              type="button"
              variant="outline"
              onClick={close}
              disabled={submitting}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={submitting}>
              {submitting
                ? "Saving..."
                : mode === "create"
                  ? "Submit Cover"
                  : "Save"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default CoverDialog;
