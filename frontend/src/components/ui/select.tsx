import * as React from 'react'
import { ChevronDown, Check } from 'lucide-react'
import { cn } from '@/lib/utils'

interface OptionData {
  value: string
  label: React.ReactNode
  disabled?: boolean
}

interface SelectProps {
  value?: string | number
  onChange?: (event: { target: { value: string } }) => void
  className?: string
  disabled?: boolean
  children?: React.ReactNode
}

/**
 * Custom dropdown that mimics the native <select> API (accepts <option> children
 * and emits an onChange with { target: { value } }) but renders its own option
 * list, so each option can be styled — e.g. green highlight on hover/keyboard focus.
 */
const Select = ({ value, onChange, className, disabled, children }: SelectProps) => {
  const [open, setOpen] = React.useState(false)
  const [activeIndex, setActiveIndex] = React.useState(-1)
  const rootRef = React.useRef<HTMLDivElement>(null)
  const listRef = React.useRef<HTMLDivElement>(null)
  const typeahead = React.useRef<{ buf: string; timer: ReturnType<typeof setTimeout> | null }>({
    buf: '',
    timer: null,
  })

  const options: OptionData[] = React.useMemo(
    () =>
      React.Children.toArray(children)
        .filter(React.isValidElement)
        .map(child => {
          const props = (child as React.ReactElement<{ value?: string | number; children?: React.ReactNode; disabled?: boolean }>).props
          return {
            value: String(props.value ?? ''),
            label: props.children,
            disabled: props.disabled,
          }
        }),
    [children],
  )

  const currentValue = String(value ?? '')
  const selected = options.find(o => o.value === currentValue)

  React.useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  React.useEffect(() => {
    if (!open) return
    const el = listRef.current?.querySelector(`[data-idx="${activeIndex}"]`) as HTMLElement | null
    el?.scrollIntoView({ block: 'nearest' })
  }, [activeIndex, open])

  const choose = (val: string) => {
    onChange?.({ target: { value: val } })
    setOpen(false)
  }

  const openMenu = () => {
    if (disabled) return
    setActiveIndex(options.findIndex(o => o.value === currentValue))
    setOpen(true)
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (disabled) return
    if (!open) {
      if (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown') {
        e.preventDefault()
        openMenu()
      }
      return
    }
    if (e.key === 'Escape') {
      setOpen(false)
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIndex(i => Math.min(options.length - 1, i + 1))
      return
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIndex(i => Math.max(0, i - 1))
      return
    }
    if (e.key === 'Enter') {
      e.preventDefault()
      const opt = options[activeIndex]
      if (opt && !opt.disabled) choose(opt.value)
      return
    }
    if (e.key.length === 1 && /\S/.test(e.key)) {
      if (typeahead.current.timer) clearTimeout(typeahead.current.timer)
      typeahead.current.buf += e.key.toLowerCase()
      const buf = typeahead.current.buf
      const idx = options.findIndex(
        o => typeof o.label === 'string' && o.label.toLowerCase().startsWith(buf),
      )
      if (idx >= 0) setActiveIndex(idx)
      typeahead.current.timer = setTimeout(() => {
        typeahead.current.buf = ''
      }, 800)
    }
  }

  return (
    <div ref={rootRef} className={cn('relative w-full', className)}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => (open ? setOpen(false) : openMenu())}
        onKeyDown={onKeyDown}
        className="flex h-10 w-full items-center justify-between gap-2 rounded-lg border border-input bg-card py-2 pl-3 pr-3 text-left text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50"
      >
        <span className="truncate">{selected ? selected.label : ''}</span>
        <ChevronDown className="size-4 shrink-0 text-muted-foreground" />
      </button>
      {open && (
        <div
          ref={listRef}
          role="listbox"
          className="absolute z-50 mt-1 max-h-60 w-full overflow-auto rounded-lg border border-border bg-card p-1 shadow-lg"
        >
          {options.map((o, idx) => {
            const isSelected = o.value === currentValue
            const isActive = idx === activeIndex
            return (
              <div
                key={`${o.value}-${idx}`}
                data-idx={idx}
                role="option"
                aria-selected={isSelected}
                onMouseEnter={() => setActiveIndex(idx)}
                onClick={() => !o.disabled && choose(o.value)}
                className={cn(
                  'flex cursor-pointer items-center justify-between gap-2 rounded-md px-3 py-2 text-sm',
                  isActive ? 'bg-primary text-primary-foreground' : 'text-foreground',
                  o.disabled && 'pointer-events-none opacity-50',
                )}
              >
                <span className="truncate">{o.label}</span>
                {isSelected && (
                  <Check className={cn('size-4 shrink-0', isActive ? 'text-primary-foreground' : 'text-primary')} />
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
Select.displayName = 'Select'

export { Select }
