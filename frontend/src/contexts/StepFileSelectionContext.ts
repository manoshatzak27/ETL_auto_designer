import { createContext, useContext } from 'react'

export interface StepFileSelectionStore {
  // stepSlug → selected file index
  indices: Record<string, number>
  // stepSlug → filename → saved config (unknown so the context is type-agnostic)
  configs: Record<string, Record<string, unknown>>
}

// Owned by ProjectWizard — survives step navigation. Using a ref means writes
// don't trigger re-renders in the parent.
export const StepFileSelectionContext = createContext<React.MutableRefObject<StepFileSelectionStore>>({
  current: { indices: {}, configs: {} },
})

export function useStepFileSelection() {
  return useContext(StepFileSelectionContext)
}
