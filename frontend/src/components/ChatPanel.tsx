/**
 * ChatPanel — floating AI chat interface for discussing and modifying generated ETL scripts.
 *
 * Opens as a fixed panel anchored to the bottom-right of the screen.
 * The user selects which table to discuss; the AI can answer questions
 * or return an updated version of the script. That version is only a proposal,
 * kept in the chat history, until the user applies or discards it.
 */
import { useState, useEffect, useRef } from 'react'
import { getChatHistory, sendChatMessage, clearChatHistory, applyChatProposal, discardChatProposal } from '../api/client'
import type { Project, ChatMessage } from '../types'
import {
  MessageSquare, X, Send, RefreshCw, Trash2,
  Bot, User, Sparkles, Code2, Check, Ban,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Select } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { useConfirm } from '../contexts/confirm'

const TABLES = [
  { key: 'location',             label: 'location.py' },
  { key: 'care_site',            label: 'care_site.py' },
  { key: 'provider',             label: 'provider.py' },
  { key: 'person',               label: 'person.py' },
  { key: 'visit_occurrence',     label: 'visit_occurrence.py' },
  { key: 'observation_period',   label: 'observation_period.py' },
  { key: 'stem_table',           label: 'stem_table.py' },
  { key: 'death',                label: 'death.py' },
  { key: 'measurement',          label: 'measurement.py' },
  { key: 'observation',          label: 'observation.py' },
  { key: 'drug_exposure',        label: 'drug_exposure.py' },
  { key: 'procedure_occurrence', label: 'procedure_occurrence.py' },
  { key: 'condition_occurrence', label: 'condition_occurrence.py' },
]

// The panel is resized from its left and top edges (it's anchored bottom-right);
// the size is a per-browser preference.
const MIN_WIDTH = 380
const MIN_HEIGHT = 360
const SIZE_KEY = 'etl-designer:chat-size'

type PanelSize = { width: number; height: number }
type ResizeEdge = 'left' | 'top' | 'corner'

function loadSize(): PanelSize | null {
  try {
    const s = JSON.parse(localStorage.getItem(SIZE_KEY) || 'null')
    return s && typeof s.width === 'number' && typeof s.height === 'number' ? s : null
  } catch {
    return null
  }
}

function saveSize(size: PanelSize | null) {
  try {
    if (size) localStorage.setItem(SIZE_KEY, JSON.stringify(size))
    else localStorage.removeItem(SIZE_KEY)
  } catch { /* storage unavailable: the size just isn't remembered */ }
}

interface Props {
  project: Project
  onUpdate: (p: Project) => void
  /** Pre-select a table when opening (e.g. from ScriptGenerator) */
  defaultTable?: string
}

export default function ChatPanel({ project, onUpdate, defaultTable }: Props) {
  const generatedTables = TABLES.filter(t => (project.generated_scripts || {})[t.key])

  const [open, setOpen] = useState(false)
  const [table, setTable] = useState(
    defaultTable && (project.generated_scripts || {})[defaultTable]
      ? defaultTable
      : generatedTables[0]?.key || '',
  )
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [error, setError] = useState('')
  const [resolving, setResolving] = useState(false)
  const [nearPageBottom, setNearPageBottom] = useState(false)
  const [size, setSize] = useState<PanelSize | null>(loadSize)
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)

  // Escape closes the panel, unless something inside it (a dropdown) or a
  // dialog over it handled the key press first.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !e.defaultPrevented) setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  const startResize = (edge: ResizeEdge) => (e: React.PointerEvent<HTMLDivElement>) => {
    const panel = panelRef.current
    if (!panel || e.button !== 0) return
    e.preventDefault()
    const rect = panel.getBoundingClientRect()
    const startX = e.clientX
    const startY = e.clientY
    // The panel can grow up to the window's left and top edges, less a margin.
    const maxWidth = rect.right - 16
    const maxHeight = rect.bottom - 16
    let next: PanelSize = { width: rect.width, height: rect.height }
    const onMove = (ev: PointerEvent) => {
      next = {
        width: edge === 'top' ? rect.width : Math.min(maxWidth, Math.max(MIN_WIDTH, rect.width + startX - ev.clientX)),
        height: edge === 'left' ? rect.height : Math.min(maxHeight, Math.max(MIN_HEIGHT, rect.height + startY - ev.clientY)),
      }
      setSize(next)
    }
    const onUp = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      document.body.style.userSelect = ''
      saveSize(next)
    }
    document.body.style.userSelect = 'none'
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }

  const resetSize = () => {
    setSize(null)
    saveSize(null)
  }

  // Track whether the page itself is scrolled to the bottom (footer nav visible),
  // so the floating panel can lift above it instead of overlapping the Next button
  useEffect(() => {
    const checkScroll = () => {
      const distanceFromBottom =
        document.documentElement.scrollHeight - (window.innerHeight + window.scrollY)
      setNearPageBottom(distanceFromBottom < 24)
    }
    checkScroll()
    window.addEventListener('scroll', checkScroll, { passive: true })
    window.addEventListener('resize', checkScroll)
    return () => {
      window.removeEventListener('scroll', checkScroll)
      window.removeEventListener('resize', checkScroll)
    }
  }, [])

  // Load chat history when panel opens
  useEffect(() => {
    if (!open) return
    setLoadingHistory(true)
    getChatHistory(project.id)
      .then(data => setMessages(data.history || []))
      .catch(() => {})
      .finally(() => setLoadingHistory(false))
  }, [open, project.id])

  // Scroll to bottom when messages change
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Focus input when opened
  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 120)
  }, [open])

  // If the selected table has no script (e.g. none were generated yet
  // when the panel opened), fall back to the first generated one as it becomes available
  useEffect(() => {
    if (table && (project.generated_scripts || {})[table]) return
    if (generatedTables.length > 0) setTable(generatedTables[0].key)
  }, [table, generatedTables, project.generated_scripts])

  // Filter messages for currently selected table
  const visibleMessages = messages.filter(
    m => !m.table || m.table === table,
  )

  // The AI's script waits in the history until it is applied or discarded, so
  // it survives a reload or a switch to another table and back.
  const hasProposal = visibleMessages.some(m => m.code_status === 'proposed')

  const confirm = useConfirm()

  const resolveProposal = async (apply: boolean) => {
    if (!apply && !(await confirm({
      title: 'Discard the proposed change?',
      description: `${table}.py stays as it is. The proposal can't be applied later; you would have to ask for it again.`,
      confirmLabel: 'Discard',
    }))) return
    setResolving(true)
    setError('')
    try {
      if (apply) {
        onUpdate(await applyChatProposal(project.id, table))
        setMessages((await getChatHistory(project.id)).history || [])
      } else {
        setMessages((await discardChatProposal(project.id, table)).history || [])
      }
    } catch (e: unknown) {
      setError(readDetail(e) || `Failed to ${apply ? 'apply' : 'discard'} the change.`)
    } finally {
      setResolving(false)
    }
  }

  const handleSend = async () => {
    const text = input.trim()
    if (!text || sending) return

    const optimistic: ChatMessage = { role: 'user', content: text, table }
    setMessages(prev => [...prev, optimistic])
    setInput('')
    setSending(true)
    setError('')

    try {
      const data = await sendChatMessage(project.id, text, table)
      setMessages(data.history || [])
    } catch (e: unknown) {
      setError(readDetail(e) || 'Failed to send message. Please try again.')
      setMessages(prev => prev.slice(0, -1))
    } finally {
      setSending(false)
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleClear = async () => {
    if (!(await confirm({
      title: 'Clear the chat history?',
      description: "Every table's conversation in this project is deleted, including any change still waiting to be applied. Scripts already changed stay as they are.",
      confirmLabel: 'Clear history',
    }))) return
    await clearChatHistory(project.id)
    setMessages([])
  }

  const hasAnyScript = generatedTables.length > 0

  return (
    <>
      {/* Floating toggle button */}
      {!open && (
        <Button
          onClick={() => setOpen(true)}
          className={cn(
            'fixed right-6 z-40 rounded-full px-4 py-3 shadow-lg font-medium text-sm transition-[bottom] duration-200',
            nearPageBottom ? 'bottom-24' : 'bottom-6',
          )}
        >
          <MessageSquare className="w-4 h-4" />
          Chat with AI
        </Button>
      )}

      {/* Panel */}
      {open && (
        <div
          ref={panelRef}
          role="dialog"
          aria-label="AI Code Assistant"
          className={cn(
            'fixed right-4 z-50 flex flex-col bg-card border border-border rounded-2xl shadow-2xl transition-[bottom] duration-200',
            !size && 'w-[640px] h-[80vh] max-h-[840px]',
            nearPageBottom ? 'bottom-24' : 'bottom-4',
          )}
          style={{
            ...(size ? { width: size.width, height: size.height } : {}),
            // Stay inside the window when it gets smaller than the saved size.
            maxWidth: 'calc(100vw - 2rem)',
            maxHeight: nearPageBottom ? 'calc(100vh - 7rem)' : 'calc(100vh - 2rem)',
          }}
        >
          {/* Resize handles: left edge, top edge, top-left corner. Double-click resets. */}
          <div
            onPointerDown={startResize('left')}
            onDoubleClick={resetSize}
            title="Drag to resize · double-click to reset"
            className="absolute left-0 top-4 bottom-4 w-1.5 -translate-x-1/2 cursor-ew-resize rounded-full hover:bg-primary/40 z-10"
          />
          <div
            onPointerDown={startResize('top')}
            onDoubleClick={resetSize}
            title="Drag to resize · double-click to reset"
            className="absolute top-0 left-4 right-4 h-1.5 -translate-y-1/2 cursor-ns-resize rounded-full hover:bg-primary/40 z-10"
          />
          <div
            onPointerDown={startResize('corner')}
            onDoubleClick={resetSize}
            title="Drag to resize · double-click to reset"
            className="absolute left-0 top-0 size-4 -translate-x-1/3 -translate-y-1/3 cursor-nwse-resize rounded-full hover:bg-primary/40 z-20"
          />

          {/* Header */}
          <div className="flex items-center gap-3 px-4 py-3 border-b border-border bg-secondary/60 rounded-t-2xl flex-shrink-0">
            <div className="w-8 h-8 rounded-full bg-accent flex items-center justify-center flex-shrink-0">
              <Sparkles className="w-4 h-4 text-amber-500" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold text-foreground">AI Code Assistant</p>
              <p className="text-xs text-muted-foreground truncate">Ask questions or request changes</p>
            </div>
            <Button
              variant="ghost"
              size="icon"
              onClick={handleClear}
              title="Clear history"
              className="w-7 h-7 text-muted-foreground hover:text-destructive hover:bg-destructive/10"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setOpen(false)}
              title="Close (Esc)"
              aria-label="Close chat"
              className="w-7 h-7 text-muted-foreground hover:text-foreground"
            >
              <X className="w-4 h-4" />
            </Button>
          </div>

          {/* Table selector */}
          <div className="px-3 py-2 border-b border-border flex-shrink-0">
            {hasAnyScript ? (
              <Select
                value={table}
                onChange={e => setTable(e.target.value)}
                className="text-xs font-mono"
              >
                {generatedTables.map(t => (
                  <option key={t.key} value={t.key}>
                    {t.label}
                  </option>
                ))}
              </Select>
            ) : (
              <p className="text-xs text-amber-600 flex items-center gap-1">
                <Code2 className="w-3 h-3" />
                Generate a script first to chat about it.
              </p>
            )}
          </div>

          {/* Messages */}
          <div className="flex-1 overflow-y-auto px-3 py-3 flex flex-col gap-3 min-h-0">
            {loadingHistory && (
              <div className="flex justify-center py-8">
                <RefreshCw className="w-4 h-4 text-muted-foreground animate-spin" />
              </div>
            )}

            {!loadingHistory && visibleMessages.length === 0 && (
              <div className="flex flex-col items-center justify-center h-full text-center gap-3 py-8">
                <div className="w-12 h-12 rounded-full bg-secondary/60 flex items-center justify-center">
                  <MessageSquare className="w-5 h-5 text-primary" />
                </div>
                <div>
                  <p className="text-sm font-medium text-foreground">No messages yet</p>
                  <p className="text-xs text-muted-foreground mt-1 max-w-[240px]">
                    Ask about the code, request changes, or get an explanation of how it works.
                  </p>
                </div>
                <div className="flex flex-col gap-1.5 w-full max-w-[260px] mt-1">
                  {[
                    'What does this script do?',
                    'Add error logging for null values',
                    'Explain the concept mapping logic',
                  ].map(suggestion => (
                    <button
                      key={suggestion}
                      onClick={() => setInput(suggestion)}
                      className="text-xs text-left text-primary hover:text-primary/80 bg-secondary/60 hover:bg-accent px-3 py-2 rounded-lg transition-colors"
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {!loadingHistory && visibleMessages.map((msg, i) => (
              <MessageBubble key={i} msg={msg} />
            ))}

            {sending && <TypingIndicator />}

            {error && (
              <p className="text-xs text-destructive text-center px-2">{error}</p>
            )}

            <div ref={bottomRef} />
          </div>

          {/* Pending code confirmation banner */}
          {hasProposal && (
            <div className="mx-3 mb-2 rounded-xl border border-amber-400/60 bg-amber-50 dark:bg-amber-950/30 px-3.5 py-3 flex-shrink-0">
              <p className="text-xs font-semibold text-amber-800 dark:text-amber-300 flex items-center gap-1.5">
                <Code2 className="w-3.5 h-3.5" />
                AI proposed a change to {table}.py — apply it?
              </p>
              <p className="text-xs text-amber-800/80 dark:text-amber-300/80 mt-0.5 mb-2">
                The script stays as it is until you apply the change.
              </p>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  onClick={() => resolveProposal(true)}
                  disabled={resolving}
                  className="h-7 text-xs gap-1.5 bg-amber-600 hover:bg-amber-700 text-white"
                >
                  <Check className="w-3 h-3" />
                  Apply changes
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => resolveProposal(false)}
                  disabled={resolving}
                  className="h-7 text-xs gap-1.5 text-muted-foreground hover:text-destructive"
                >
                  <Ban className="w-3 h-3" />
                  Discard
                </Button>
              </div>
            </div>
          )}

          {/* Input */}
          <div className="px-3 py-3 border-t border-border flex-shrink-0">
            <div className="flex items-end gap-2">
              <Textarea
                ref={inputRef}
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Ask about the code or request changes… (Enter to send)"
                rows={2}
                disabled={!hasAnyScript}
                className="flex-1 resize-none text-sm rounded-xl px-3 py-2.5 min-h-0"
              />
              <Button
                size="icon"
                onClick={handleSend}
                disabled={!input.trim() || sending || !hasAnyScript}
                className="flex-shrink-0 w-9 h-9 rounded-xl"
              >
                {sending
                  ? <RefreshCw className="w-4 h-4 animate-spin" />
                  : <Send className="w-4 h-4" />
                }
              </Button>
            </div>
            <p className="text-xs text-muted-foreground mt-1.5 text-center">
              Shift+Enter for newline · Enter to send
            </p>
          </div>
        </div>
      )}
    </>
  )
}

const CODE_STATUS_LABEL: Record<NonNullable<ChatMessage['code_status']>, string> = {
  proposed: 'Proposed change — waiting for you to apply or discard it.',
  applied: 'Change applied to the script.',
  discarded: 'Change discarded.',
  superseded: 'Change replaced by a newer proposal.',
}

function readDetail(e: unknown): string {
  const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return typeof detail === 'string' ? detail : ''
}

function MessageBubble({ msg }: { msg: ChatMessage }) {
  const isUser = msg.role === 'user'
  // Messages from before proposals existed: their code was applied on arrival.
  const codeStatus = msg.code_status ?? (msg.code_updated ? 'applied' : undefined)

  return (
    <div className={cn('flex gap-2', isUser ? 'flex-row-reverse' : 'flex-row')}>
      {/* Avatar */}
      <div className={cn(
        'w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0 mt-0.5',
        isUser ? 'bg-accent' : 'bg-muted',
      )}>
        {isUser
          ? <User className="w-3.5 h-3.5 text-primary" />
          : <Bot className="w-3.5 h-3.5 text-muted-foreground" />
        }
      </div>

      {/* Bubble */}
      <div className={cn(
        'max-w-[80%] rounded-2xl px-3.5 py-2.5 text-sm',
        isUser
          ? 'bg-primary text-primary-foreground rounded-tr-sm'
          : 'bg-muted text-foreground rounded-tl-sm',
      )}>
        {codeStatus && (
          <div className={cn(
            'flex items-center gap-1.5 mb-2 text-xs rounded-lg px-2.5 py-1.5 font-medium',
            codeStatus === 'applied' && 'text-green-700 bg-green-100',
            codeStatus === 'proposed' && 'text-amber-800 bg-amber-100',
            (codeStatus === 'discarded' || codeStatus === 'superseded') && 'text-muted-foreground bg-background/60',
          )}>
            <Code2 className="w-3 h-3" />
            {CODE_STATUS_LABEL[codeStatus]}
          </div>
        )}
        <FormattedMessage content={msg.content} isUser={isUser} />
      </div>
    </div>
  )
}

function FormattedMessage({ content, isUser }: { content: string; isUser: boolean }) {
  // Split on code fences and render them distinctly
  const parts = content.split(/(```(?:python)?\n[\s\S]*?```)/g)

  return (
    <div className="flex flex-col gap-2">
      {parts.map((part, i) => {
        if (part.startsWith('```')) {
          const code = part.replace(/^```(?:python)?\n/, '').replace(/```$/, '')
          return (
            <pre
              key={i}
              className={cn(
                'text-xs font-mono rounded-lg px-3 py-2.5 overflow-x-auto whitespace-pre',
                isUser ? 'bg-primary/80 text-primary-foreground' : 'bg-gray-800 text-gray-100',
              )}
            >
              {code}
            </pre>
          )
        }
        return (
          <span key={i} className="whitespace-pre-wrap leading-relaxed">
            {part}
          </span>
        )
      })}
    </div>
  )
}

function TypingIndicator() {
  return (
    <div className="flex gap-2">
      <div className="w-7 h-7 rounded-full bg-muted flex items-center justify-center flex-shrink-0">
        <Bot className="w-3.5 h-3.5 text-muted-foreground" />
      </div>
      <div className="bg-muted rounded-2xl rounded-tl-sm px-4 py-3">
        <div className="flex gap-1 items-center h-4">
          {[0, 1, 2].map(i => (
            <span
              key={i}
              className="w-1.5 h-1.5 bg-muted-foreground rounded-full animate-bounce"
              style={{ animationDelay: `${i * 150}ms` }}
            />
          ))}
        </div>
      </div>
    </div>
  )
}
