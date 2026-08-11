import { Star } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  Carousel,
  CarouselContent,
  CarouselItem,
  CarouselNext,
  CarouselPrevious,
  type CarouselApi,
} from "@/components/ui/carousel";
import imageNotAvailable from "@/assets/image-not-available.jpg";
import type { EntryGalleryImage } from "./types";

export function EntryImageGalleryCard({
  images,
  showSubjectBadge,
  placeholderSubjectLabel,
  carouselApi,
  setCarouselApi,
  currentIndex,
  canSetDefaultImage,
  settingDefaultImage,
  onSetDefaultImage,
}: {
  images: EntryGalleryImage[];
  /** When true, show subject label badge (marking detail). Cover detail omits this. */
  showSubjectBadge: boolean;
  placeholderSubjectLabel?: string;
  carouselApi: CarouselApi | undefined;
  setCarouselApi: (api: CarouselApi | undefined) => void;
  currentIndex: number;
  canSetDefaultImage?: boolean;
  settingDefaultImage?: boolean;
  onSetDefaultImage?: (index: number) => void;
}) {
  const slides: EntryGalleryImage[] =
    images.length > 0
      ? images
      : [
          {
            imageUrl: imageNotAvailable,
            subjectLabel: placeholderSubjectLabel,
            isDefault: false,
            isTracing: false,
            imageId: null,
          },
        ];

  return (
    <Card className="shadow-archival-lg">
      <CardContent className="p-6">
        <Carousel setApi={setCarouselApi} className="w-full">
          <CarouselContent>
            {slides.map((img, index) => {
              const src = img.imageUrl || imageNotAvailable;
              const alt = img.originalFilename || `Image ${index + 1}`;
              const isPlaceholder = !img.imageUrl;
              const showDefaultAction =
                canSetDefaultImage === true &&
                onSetDefaultImage != null &&
                img.imageId != null &&
                !isPlaceholder;
              const imageFrame = (
                <div className="relative flex w-full aspect-[4/3] items-center justify-center rounded border border-border bg-muted overflow-hidden">
                  <img src={src} alt={alt} className="w-full h-full object-contain" />
                  <div className="absolute top-2 left-2 flex flex-wrap items-center gap-1">
                    {showSubjectBadge && img.subjectLabel && (
                      <Badge variant="secondary">{img.subjectLabel}</Badge>
                    )}
                    {!isPlaceholder && img.isTracing && (
                      <Badge variant="secondary">Tracing</Badge>
                    )}
                  </div>
                </div>
              );
              return (
                <CarouselItem key={index}>
                  <div className="relative">
                    {img.imageUrl ? (
                      <a
                        href={img.imageUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        aria-label={`Open ${alt} in new tab`}
                        className="block"
                      >
                        {imageFrame}
                      </a>
                    ) : (
                      imageFrame
                    )}
                    {showDefaultAction && (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            type="button"
                            variant="outline"
                            size="icon"
                            className={
                              img.isDefault
                                ? "absolute right-2 top-2 h-8 w-8 border-amber-400 bg-amber-100 text-amber-700 hover:bg-amber-100 hover:text-amber-700 disabled:opacity-100"
                                : "absolute right-2 top-2 h-8 w-8 bg-background/90"
                            }
                            aria-label={
                              img.isDefault
                                ? "Default catalog image"
                                : "Set as default catalog image"
                            }
                            disabled={settingDefaultImage || img.isDefault}
                            onClick={() => onSetDefaultImage(index)}
                          >
                            <Star className={`h-4 w-4 ${img.isDefault ? "fill-amber-500 text-amber-500" : ""}`} />
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent>
                          {img.isDefault ? "Default catalog image" : "Set as default catalog image"}
                        </TooltipContent>
                      </Tooltip>
                    )}
                  </div>
                </CarouselItem>
              );
            })}
          </CarouselContent>
          {images.length > 1 && (
            <>
              <CarouselPrevious className="left-2" />
              <CarouselNext className="right-2" />
            </>
          )}
        </Carousel>
        {images.length > 1 && (
          <div className="flex justify-center gap-2 mt-4">
            {images.map((_, index) => (
              <button
                key={index}
                type="button"
                onClick={() => carouselApi?.scrollTo(index)}
                className={`h-2 rounded-full transition-all ${index === currentIndex ? "w-6 bg-primary" : "w-2 bg-muted-foreground/30"}`}
                aria-label={`Go to image ${index + 1}`}
              />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
