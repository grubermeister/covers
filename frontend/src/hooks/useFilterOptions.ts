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
}

export const useFilterOptions = (options?: UseFilterOptionsOptions): UseFilterOptionsReturn => {
  const [colorOptions, setColorOptions] = useState<ColorOption[]>([]);
  const [shapeOptions, setShapeOptions] = useState<ShapeOption[]>([]);
  const [stateOptions, setStateOptions] = useState<StateOption[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const assignedStatesOnly = options?.assignedStatesOnly ?? false;

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
      setShapeOptions(shapes.map((s) => ({ value: String(s.id), label: s.name })));
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
  }, [assignedStatesOnly]);

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
