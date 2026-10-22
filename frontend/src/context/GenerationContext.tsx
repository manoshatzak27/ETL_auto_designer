import { createContext, useContext, useRef, useState } from 'react'

interface GenerationContextValue {
  isAnyGenerating: boolean
  acquireLock: () => boolean
  releaseLock: () => void
}

const GenerationContext = createContext<GenerationContextValue>({
  isAnyGenerating: false,
  acquireLock: () => true,
  releaseLock: () => {},
})

export function GenerationProvider({ children }: { children: React.ReactNode }) {
  const [isAnyGenerating, setIsAnyGenerating] = useState(false)
  const lockRef = useRef(false)

  const acquireLock = (): boolean => {
    if (lockRef.current) return false
    lockRef.current = true
    setIsAnyGenerating(true)
    return true
  }

  const releaseLock = () => {
    lockRef.current = false
    setIsAnyGenerating(false)
  }

  return (
    <GenerationContext.Provider value={{ isAnyGenerating, acquireLock, releaseLock }}>
      {children}
    </GenerationContext.Provider>
  )
}

export const useGeneration = () => useContext(GenerationContext)
