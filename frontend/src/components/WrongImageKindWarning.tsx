import { AlertTriangle } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import {
  COVER_FORM_MARKING_IMAGE_WARNING,
  MARKING_FORM_COVER_IMAGE_WARNING,
  WRONG_IMAGE_KIND_OVERRIDE_LABEL,
} from "@/labels/guidelines";

interface WrongImageKindWarningProps {
  /** What this form collects. Decides which way the warning reads. */
  expected: "MARKING" | "COVER";
  /** How many selected images look like the other kind. */
  count: number;
  acknowledged: boolean;
  onAcknowledgedChange: (value: boolean) => void;
}

/**
 * Advisory shown when uploaded images look like the wrong kind for this form
 * (issue #76).
 *
 * ~98% of the mis-slotted images in the catalog came from the v1 import rather
 * than from contributors, so this is not the main repair -- it stops the small
 * live trickle while that backfill happens (#78).
 *
 * The classification is a guess from pixel dimensions, so ticking the
 * acknowledgement always clears the block. Rendering nothing when count is 0
 * keeps the form quiet in the normal case.
 */
export function WrongImageKindWarning({
  expected,
  count,
  acknowledged,
  onAcknowledgedChange,
}: WrongImageKindWarningProps) {
  if (count < 1) return null;

  const copy =
    expected === "MARKING"
      ? MARKING_FORM_COVER_IMAGE_WARNING
      : COVER_FORM_MARKING_IMAGE_WARNING;
  const checkboxId = `wrong-image-kind-ack-${expected.toLowerCase()}`;

  return (
    <Alert variant="warning" className="mt-3" data-testid="wrong-image-kind-warning">
      <AlertTriangle className="h-4 w-4" />
      <AlertTitle>{copy.title}</AlertTitle>
      <AlertDescription>
        <p>{copy.body}</p>
        {count > 1 && (
          <p className="mt-1 text-xs opacity-80">
            {count} of the images you selected look this way.
          </p>
        )}
        <div className="mt-3 flex items-center gap-2">
          <Checkbox
            id={checkboxId}
            checked={acknowledged}
            onCheckedChange={(value) => onAcknowledgedChange(value === true)}
          />
          <Label htmlFor={checkboxId} className="cursor-pointer text-sm font-normal">
            {WRONG_IMAGE_KIND_OVERRIDE_LABEL}
          </Label>
        </div>
      </AlertDescription>
    </Alert>
  );
}

export default WrongImageKindWarning;
