"use client";

import * as React from "react";
import { Check, ChevronsUpDown, Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";

export interface SearchableSelectOption {
  value: string;
  label: string;
}

interface SearchableSelectProps {
  options: SearchableSelectOption[];
  value: string;
  onValueChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
  allOption?: { value: string; label: string };
  emptyMessage?: string;
  searchPlaceholder?: string;
  loading?: boolean;
  error?: boolean;
  errorMessage?: string;
  id?: string;
  triggerClassName?: string;
  contentClassName?: string;
  "aria-label"?: string;
}

/**
 * A searchable dropdown (combobox) for long option lists.
 * Use for State, Marking Type, Color filters, etc.
 */
export function SearchableSelect({
  options,
  value,
  onValueChange,
  placeholder = "Select...",
  disabled = false,
  allOption,
  emptyMessage = "No option found.",
  searchPlaceholder = "Search...",
  loading = false,
  error = false,
  errorMessage,
  id,
  triggerClassName,
  contentClassName,
  "aria-label": ariaLabel,
}: SearchableSelectProps) {
  const [open, setOpen] = React.useState(false);

  const displayLabel =
    value === "all" || !value
      ? allOption?.label ?? placeholder
      : options.find((o) => o.value === value)?.label ?? value;

  const effectiveOptions = allOption
    ? [{ value: allOption.value, label: allOption.label }, ...options]
    : options;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          id={id}
          variant="outline"
          role="combobox"
          aria-expanded={open}
          aria-label={ariaLabel ?? placeholder}
           disabled={disabled || loading}
          className={cn(
            "w-full justify-between font-normal h-10 px-3 py-2 text-sm",
            triggerClassName,
          )}
        >
          {loading ? (
            <span className="flex items-center gap-2">
              <Loader2 className="h-4 w-4 animate-spin shrink-0" />
              Loading...
            </span>
          ) : error ? (
            <span className="text-destructive">
              {errorMessage ?? "Failed to load"}
            </span>
          ) : (
            <span className="truncate">{displayLabel}</span>
          )}
          <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        className={cn("w-[var(--radix-popover-trigger-width)] p-0", contentClassName)}
        align="start"
        sideOffset={4}
      >
        <Command shouldFilter={true}>
          <CommandInput placeholder={searchPlaceholder} />
          <CommandList>
            <CommandEmpty>{emptyMessage}</CommandEmpty>
            <CommandGroup>
              {effectiveOptions.map((option) => (
                <CommandItem
                  key={option.value}
                  value={option.label}
                  onSelect={() => {
                    onValueChange(option.value);
                    setOpen(false);
                  }}
                >
                  <Check
                    className={cn(
                      "mr-2 h-4 w-4",
                      value === option.value ? "opacity-100" : "opacity-0",
                    )}
                  />
                  {option.label}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
