import { ArrowDown, ArrowUp, Replace, Star, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import imageNotAvailable from "@/assets/image-not-available.jpg";
import type { CarouselApi } from "@/components/ui/carousel";
import type { EntryGalleryImage } from "./types";

export function EntryAssociatedThumbnailsCard({
  images,
  carouselApi,
  currentIndex,
  emptyMessage,
  canReorder,
  reorderingImages,
  deletingImageId,
  onMoveBy,
  onSetDefault,
  onDeleteImage,
  onMoveImage,
  moveImageLabel,
}: {
  images: EntryGalleryImage[];
  carouselApi: CarouselApi | undefined;
  currentIndex: number;
  emptyMessage: string;
  canReorder?: boolean;
  reorderingImages?: boolean;
  deletingImageId?: number | null;
  onMoveBy?: (index: number, offset: -1 | 1) => void;
  onSetDefault?: (index: number) => void;
  onDeleteImage?: (index: number) => void;
  /** Reassign the image to another subject (issue #48), e.g. cover → marking. */
  onMoveImage?: (index: number) => void;
  moveImageLabel?: string;
}) {
  return (
    <Card className="shadow-archival-md">
      <CardHeader>
        <CardTitle className="font-heading text-lg">Associated Thumbnails</CardTitle>
      </CardHeader>
      <CardContent>
        {images.length === 0 ? (
          <p className="text-sm text-muted-foreground">{emptyMessage}</p>
        ) : (
          <div className="flex gap-3 overflow-x-auto pb-1">
            {images.map((img, idx) => (
              <div
                key={`${img.imageId ?? img.originalFilename ?? "img"}-${idx}`}
                className="flex flex-col items-center gap-1 shrink-0"
              >
                <button
                  type="button"
                  onClick={() => carouselApi?.scrollTo(idx)}
                  aria-label={`Show image ${idx + 1}`}
                  className={`relative h-16 w-16 rounded border overflow-hidden transition-all ${idx === currentIndex ? "border-primary ring-2 ring-primary" : "border-border"}`}
                >
                  <img
                    src={img.imageUrl || imageNotAvailable}
                    alt={img.originalFilename || `Thumbnail ${idx + 1}`}
                    className="h-full w-full object-cover"
                  />
                </button>
                {(canReorder || onDeleteImage || onMoveImage) && (
                  <div className="flex items-center gap-0.5">
                    {canReorder && onMoveBy && onSetDefault && (
                      <>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="h-6 w-6"
                          aria-label="Move thumbnail left"
                          disabled={reorderingImages || idx === 0}
                          onClick={() => onMoveBy(idx, -1)}
                        >
                          <ArrowUp className="h-3 w-3 -rotate-90" />
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="h-6 w-6"
                          aria-label="Move thumbnail right"
                          disabled={reorderingImages || idx === images.length - 1}
                          onClick={() => onMoveBy(idx, 1)}
                        >
                          <ArrowDown className="h-3 w-3 -rotate-90" />
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className={
                            img.isDefault
                              ? "h-6 w-6 text-amber-600 hover:text-amber-600 disabled:opacity-100"
                              : "h-6 w-6"
                          }
                          aria-label="Set as default catalog thumbnail"
                          disabled={reorderingImages || img.isDefault}
                          onClick={() => onSetDefault(idx)}
                        >
                          <Star className={`h-3 w-3 ${img.isDefault ? "fill-amber-500 text-amber-500" : ""}`} />
                        </Button>
                      </>
                    )}
                    {onMoveImage && img.imageId != null && (
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="h-6 w-6"
                        aria-label={moveImageLabel ?? "Move image to another record"}
                        title={moveImageLabel ?? "Move image"}
                        disabled={reorderingImages}
                        onClick={() => onMoveImage(idx)}
                      >
                        <Replace className="h-3 w-3" />
                      </Button>
                    )}
                    {onDeleteImage && img.imageId != null && (
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="h-6 w-6 text-destructive hover:text-destructive"
                        aria-label="Delete image"
                        title="Delete image"
                        disabled={reorderingImages || deletingImageId === img.imageId}
                        onClick={() => onDeleteImage(idx)}
                      >
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
