import { useState, useEffect, useCallback } from 'react';
import { ColorOption } from '@/lib/api';
import { getColors } from '@/services/colors';
import { getShapes } from '@/services/shapes';
import { getRegions } from '@/services/regions';

interface ShapeOption {
  value: string;
  label: string;
}

interface StateOption {
  value: string;
  label: string;
}

interface UseFilterOptionsReturn {
  colorOptions: ColorOption[];
  shapeOptions: ShapeOption[];
  stateOptions: StateOption[];
  isLoading: boolean;
  error: string | null;
  refetch: () => Promise<void>;
}

// function mapToColorOption(name: string): ColorOption {
//   const value = name.toLowerCase().trim();
//   return { value: value || name, label: name };
// }

interface UseFilterOptionsOptions {
  /** When true, only states assigned to the user (Dashboard). When false, all states (Search). */
  assignedStatesOnly?: boolean;
  /**
   * What a shape option's `value` carries. The two consumers need different
   * things and neither can be changed to suit the other (issue #109):
   *
   * - "id" (default) -- Catalog Search sends it as `?shape=<id>` to
   *   MarkingListFilter.shape, a NumberFilter. A name there is an HTTP 400.
   * - "name" -- the dashboard queue matches `submitted_data.shape`, which
   *   holds the Shape NAME verbatim ("C - Circle"). Contributions have no
   *   shape FK to compare against.
   *
   * The queue's shape filter had never matched anything: it was handed ids and
   * compared them against names.
   */
  shapeValues?: "id" | "name";
}

export const useFilterOptions = (options?: UseFilterOptionsOptions): UseFilterOptionsReturn => {
  const [colorOptions, setColorOptions] = useState<ColorOption[]>([]);
  const [shapeOptions, setShapeOptions] = useState<ShapeOption[]>([]);
  const [stateOptions, setStateOptions] = useState<StateOption[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const assignedStatesOnly = options?.assignedStatesOnly ?? false;
  const shapeValues = options?.shapeValues ?? "id";

  const fetchOptions = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [colors, shapes, states] = await Promise.all([
        getColors(),
        getShapes(),
        getRegions(assignedStatesOnly),
      ]);
      setColorOptions(colors.map((c) => ({ value: c.name, label: c.name })));
      setShapeOptions(shapes.map((s) => ({
        value: shapeValues === "name" ? s.name : String(s.id),
        label: s.name,
      })));
      setStateOptions(states);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to fetch filter options';
      setError(errorMessage);
      console.error('Error fetching filter options:', errorMessage);
      setColorOptions([]);
      setShapeOptions([]);
      setStateOptions([]);
    } finally {
      setIsLoading(false);
    }
  }, [assignedStatesOnly, shapeValues]);

  useEffect(() => {
    fetchOptions();
  }, [fetchOptions]);

  return {
    colorOptions,
    shapeOptions,
    stateOptions,
    isLoading,
    error,
    refetch: fetchOptions,
  };
};

export default useFilterOptions;
