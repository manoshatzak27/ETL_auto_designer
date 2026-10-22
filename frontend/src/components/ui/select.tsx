import * as React from 'react'
import { ChevronDown, Check, Search } from 'lucide-react'
import { cn } from '@/lib/utils'

interface OptionData {
  value: string
  label: React.ReactNode
  /** The label as plain text, for searching. */
  text: string
  disabled?: boolean
}

interface SelectProps {
  value?: string | number
  onChange?: (event: { target: { value: string } }) => void
  className?: string
  disabled?: boolean
  children?: React.ReactNode
}

// Lists this long get a search box; shorter ones are quicker to scan.
const SEARCH_MIN_OPTIONS = 8

function nodeText(node: React.ReactNode): string {
  if (node == null || typeof node === 'boolean') return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(nodeText).join('')
  if (React.isValidElement(node)) return nodeText((node.props as { children?: React.ReactNode }).children)
  return ''
}

/**
 * Custom dropdown that mimics the native <select> API (accepts <option> children
 * and emits an onChange with { target: { value } }) but renders its own option
 * list, so each option can be styled — e.g. green highlight on hover/keyboard focus.
 *
 * Long lists get a search box. Options with an empty value ("— not mapped —")
 * stay pinned above the results, so clearing a field never needs a search.
 */
const Select = ({ value, onChange, className, disabled, children }: SelectProps) => {
  const [open, setOpen] = React.useState(false)
  const [activeIndex, setActiveIndex] = React.useState(-1)
  const [query, setQuery] = React.useState('')
  const rootRef = React.useRef<HTMLDivElement>(null)
  const buttonRef = React.useRef<HTMLButtonElement>(null)
  const searchRef = React.useRef<HTMLInputElement>(null)
  const listRef = React.useRef<HTMLDivElement>(null)
  const listId = React.useId()
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
            text: nodeText(props.children),
            disabled: props.disabled,
          }
        }),
    [children],
  )

  const searchable = options.length >= SEARCH_MIN_OPTIONS
  const pinned = searchable ? options.filter(o => o.value === '') : []
  const q = query.trim().toLowerCase()
  const matches = searchable
    ? options.filter(o => o.value !== '' && (!q || o.text.toLowerCase().includes(q) || o.value.toLowerCase().includes(q)))
    : options
  // What the keyboard moves through, in display order.
  const visible = [...pinned, ...matches]

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
    const el = searchRef.current
    if (!open || !searchable || !el) return
    el.focus()
    // After a letter typed on the closed dropdown, so the next one follows it.
    el.setSelectionRange(el.value.length, el.value.length)
  }, [open, searchable])

  React.useEffect(() => {
    if (!open) return
    const el = listRef.current?.querySelector(`[data-idx="${activeIndex}"]`) as HTMLElement | null
    el?.scrollIntoView({ block: 'nearest' })
  }, [activeIndex, open])

  const close = (refocus: boolean) => {
    setOpen(false)
    if (refocus) buttonRef.current?.focus()
  }

  const choose = (val: string) => {
    onChange?.({ target: { value: val } })
    close(true)
  }

  const openMenu = (initialQuery = '') => {
    if (disabled) return
    setQuery(initialQuery)
    // With a search typed, start on the first match so Enter takes it.
    setActiveIndex(initialQuery ? pinned.length : visibleIndexOf(currentValue))
    setOpen(true)
  }

  // The index of `val` in the unfiltered list (the menu opens with no search).
  const visibleIndexOf = (val: string) => {
    const all = searchable ? [...pinned, ...options.filter(o => o.value !== '')] : options
    return all.findIndex(o => o.value === val)
  }

  const onSearchChange = (text: string) => {
    setQuery(text)
    setActiveIndex(text.trim() ? pinned.length : visibleIndexOf(currentValue))
  }

  // Arrow keys, Enter, Escape and Tab while the menu is open (from the search
  // box or, for a short list, the button).
  const navigate = (e: React.KeyboardEvent): boolean => {
    if (e.key === 'Escape') {
      // Handled: a panel this sits in shouldn't close on the same key press.
      e.preventDefault()
      close(true)
      return true
    }
    if (e.key === 'Tab') {
      setOpen(false)
      return true
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIndex(i => Math.min(visible.length - 1, i + 1))
      return true
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIndex(i => Math.max(0, i - 1))
      return true
    }
    if (e.key === 'Enter') {
      e.preventDefault()
      const opt = visible[activeIndex]
      if (opt && !opt.disabled) choose(opt.value)
      return true
    }
    return false
  }

  const onButtonKeyDown = (e: React.KeyboardEvent) => {
    if (disabled) return
    if (!open) {
      if (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown') {
        e.preventDefault()
        openMenu()
      } else if (searchable && e.key.length === 1 && /\S/.test(e.key) && !e.ctrlKey && !e.metaKey && !e.altKey) {
        // Typing on the closed dropdown starts a search.
        e.preventDefault()
        openMenu(e.key)
      }
      return
    }
    if (navigate(e)) return
    if (!searchable && e.key.length === 1 && /\S/.test(e.key)) {
      if (typeahead.current.timer) clearTimeout(typeahead.current.timer)
      typeahead.current.buf += e.key.toLowerCase()
      const buf = typeahead.current.buf
      const idx = visible.findIndex(o => o.text.toLowerCase().startsWith(buf))
      if (idx >= 0) setActiveIndex(idx)
      typeahead.current.timer = setTimeout(() => {
        typeahead.current.buf = ''
      }, 800)
    }
  }

  const renderOption = (o: OptionData, idx: number) => {
    const isSelected = o.value === currentValue
    const isActive = idx === activeIndex
    return (
      <div
        key={`${o.value}-${idx}`}
        id={`${listId}-${idx}`}
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
  }

  return (
    <div ref={rootRef} className={cn('relative w-full', className)}>
      <button
        ref={buttonRef}
        type="button"
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        onClick={() => (open ? setOpen(false) : openMenu())}
        onKeyDown={onButtonKeyDown}
        className="flex h-10 w-full items-center justify-between gap-2 rounded-lg border border-input bg-card py-2 pl-3 pr-3 text-left text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50"
      >
        <span className="truncate">{selected ? selected.label : ''}</span>
        <ChevronDown className="size-4 shrink-0 text-muted-foreground" />
      </button>
      {open && (
        <div className="absolute z-50 mt-1 w-full rounded-lg border border-border bg-card shadow-lg">
          {searchable && (
            <div className="border-b border-border p-1">
              <div className="relative">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
                <input
                  ref={searchRef}
                  type="text"
                  role="combobox"
                  aria-expanded
                  aria-controls={listId}
                  aria-autocomplete="list"
                  aria-activedescendant={activeIndex >= 0 ? `${listId}-${activeIndex}` : undefined}
                  value={query}
                  onChange={e => onSearchChange(e.target.value)}
                  onKeyDown={navigate}
                  placeholder={`Search ${options.length - pinned.length} options…`}
                  className="h-8 w-full rounded-md bg-muted/50 pl-8 pr-2 text-sm outline-none placeholder:text-muted-foreground focus:bg-muted"
                />
              </div>
            </div>
          )}
          <div ref={listRef} id={listId} role="listbox" className="p-1">
            {/* Pinned outside the scrolling area, so they're always in reach. */}
            {pinned.length > 0 && (
              <div className="mb-1 border-b border-border pb-1">
                {pinned.map((o, i) => renderOption(o, i))}
              </div>
            )}
            <div className="max-h-60 overflow-auto">
              {matches.map((o, i) => renderOption(o, pinned.length + i))}
              {matches.length === 0 && (
                <p className="px-3 py-2 text-sm text-muted-foreground">No options match “{query.trim()}”.</p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
Select.displayName = 'Select'

export { Select }
