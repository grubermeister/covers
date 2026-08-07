import { useCallback, useRef, useState } from "react";
import ReactCrop, { type Crop, type PixelCrop } from "react-image-crop";
import "react-image-crop/dist/ReactCrop.css";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cropImage } from "@/services/markings";

interface CropImageDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  imageId: number | null;
  imageUrl: string | null;
  /** Called after a crop is saved, so the caller can refetch the record. */
  onCropped: () => void | Promise<void>;
}

/** Smallest crop worth saving, in source pixels. Below this it is a misclick. */
const MIN_CROP_PX = 8;

/**
 * Lets an editor cut the marking out of a scan of a whole cover (issue #77).
 *
 * The catalog has a lot of whole-cover scans sitting in marking image slots.
 * The full scan belongs on a Cover, but moving it first would leave the marking
 * with no image at all, so the crop has to happen before the move.
 *
 * react-image-crop reports the selection in *displayed* pixels. The image is
 * scaled down to fit the dialog, so the selection is converted back to natural
 * pixels before it is sent -- the backend validates against the real bounds and
 * would otherwise reject or mis-crop.
 */
export function CropImageDialog({
  open,
  onOpenChange,
  imageId,
  imageUrl,
  onCropped,
}: CropImageDialogProps) {
  const [crop, setCrop] = useState<Crop>();
  const [pixelCrop, setPixelCrop] = useState<PixelCrop | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);

  const reset = useCallback(() => {
    setCrop(undefined);
    setPixelCrop(null);
    setError(null);
  }, []);

  const handleOpenChange = (next: boolean) => {
    if (busy) return;
    if (!next) reset();
    onOpenChange(next);
  };

  const handleSave = async () => {
    const img = imgRef.current;
    if (!imageId || !pixelCrop || !img) return;
    // displayed px -> natural px
    const scaleX = img.naturalWidth / img.width;
    const scaleY = img.naturalHeight / img.height;
    const rect = {
      x: pixelCrop.x * scaleX,
      y: pixelCrop.y * scaleY,
      width: pixelCrop.width * scaleX,
      height: pixelCrop.height * scaleY,
    };
    if (rect.width < MIN_CROP_PX || rect.height < MIN_CROP_PX) {
      setError("Drag a larger area to crop.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await cropImage(imageId, rect);
      if (result.ok === false) {
        setError(result.message);
        return;
      }
      await onCropped();
      reset();
      onOpenChange(false);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>Crop the marking out of this image</DialogTitle>
          <DialogDescription>
            Drag a box around the marking. The cropped area is added as a new
            image on this record; the original is left untouched, so you can
            still move it to a cover afterwards.
          </DialogDescription>
        </DialogHeader>

        {imageUrl ? (
          <div className="max-h-[60vh] overflow-auto">
            <ReactCrop
              crop={crop}
              onChange={(next) => setCrop(next)}
              onComplete={(next) => setPixelCrop(next)}
              minWidth={MIN_CROP_PX}
              minHeight={MIN_CROP_PX}
            >
              {/* crossOrigin is unset: media is served same-origin. */}
              <img
                ref={imgRef}
                src={imageUrl}
                alt="Select the marking to crop"
                className="max-w-full"
              />
            </ReactCrop>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">Image unavailable.</p>
        )}

        {error && <p className="text-sm text-destructive">{error}</p>}

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => handleOpenChange(false)}
          >
            Cancel
          </Button>
          <Button
            type="button"
            disabled={busy || !pixelCrop || !pixelCrop.width || !pixelCrop.height}
            onClick={() => void handleSave()}
          >
            {busy ? "Saving..." : "Save crop"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default CropImageDialog;
