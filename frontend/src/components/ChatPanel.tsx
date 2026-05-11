/**
 * ChatPanel — floating AI chat interface for discussing and modifying generated ETL scripts.
 *
 * Opens as a fixed panel anchored to the bottom-right of the screen.
 * The user selects which table to discuss; the AI can answer questions
 * or return an updated version of the script automatically applied to the project.
 */
import { useState, useEffect, useRef } from 'react'
import { getChatHistory, sendChatMessage, clearChatHistory } from '../api/client'
import type { Project, ChatMessage } from '../types'
import {
  MessageSquare, X, Send, RefreshCw, Trash2,
  ChevronDown, Bot, User, Sparkles, Code2,
} from 'lucide-react'
import clsx from 'clsx'

const TABLES = [
  { key: 'location',           label: 'location.py' },
  { key: 'care_site',          label: 'care_site.py' },
  { key: 'provider',           label: 'provider.py' },
  { key: 'person',             label: 'person.py' },
  { key: 'visit_occurrence',   label: 'visit_occurrence.py' },
  { key: 'observation_period', label: 'observation_period.py' },
  { key: 'stem_table',         label: 'stem_table.py' },
  { key: 'death',              label: 'death.py' },
]

interface Props {
  project: Project
  onUpdate: (p: Project) => void
  /** Pre-select a table when opening (e.g. from ScriptGenerator) */
  defaultTable?: string
}

export default function ChatPanel({ project, onUpdate, defaultTable }: Props) {
  const [open, setOpen] = useState(false)
  const [table, setTable] = useState(defaultTable || 'person')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [error, setError] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

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

  // Filter messages for currently selected table
  const visibleMessages = messages.filter(
    m => !m.table || m.table === table,
  )

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

      const aiMsg: ChatMessage = {
        role: 'assistant',
        content: data.response,
        table,
        code_updated: data.code_updated,
      }
      setMessages(prev => [...prev, aiMsg])

      if (data.code_updated && data.generated_scripts) {
        onUpdate({ ...project, generated_scripts: data.generated_scripts })
      }
    } catch {
      setError('Failed to send message. Please try again.')
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
    if (!confirm('Clear all chat history for this project?')) return
    await clearChatHistory(project.id)
    setMessages([])
  }

  const hasScript = !!(project.generated_scripts || {})[table]

  return (
    <>
      {/* Floating toggle button */}
      {!open && (
        <button
          onClick={() => setOpen(true)}
          className="fixed bottom-20 right-6 z-40 flex items-center gap-2 px-4 py-3 bg-blue-600 text-white rounded-full shadow-lg hover:bg-blue-700 transition-all font-medium text-sm"
        >
          <MessageSquare className="w-4 h-4" />
          Chat with AI
        </button>
      )}

      {/* Panel */}
      {open && (
        <div className="fixed bottom-[72px] right-4 z-50 flex flex-col w-[420px] h-[580px] bg-white border border-gray-200 rounded-2xl shadow-2xl">
          {/* Header */}
          <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-100 bg-gradient-to-r from-blue-50 to-indigo-50 rounded-t-2xl flex-shrink-0">
            <div className="w-8 h-8 rounded-full bg-blue-100 flex items-center justify-center flex-shrink-0">
              <Sparkles className="w-4 h-4 text-blue-600" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold text-gray-800">AI Code Assistant</p>
              <p className="text-xs text-gray-500 truncate">Ask questions or request changes</p>
            </div>
            <button
              onClick={handleClear}
              title="Clear history"
              className="p-1.5 text-gray-400 hover:text-red-500 rounded-lg hover:bg-red-50 transition-colors"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={() => setOpen(false)}
              className="p-1.5 text-gray-400 hover:text-gray-700 rounded-lg hover:bg-gray-100 transition-colors"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* Table selector */}
          <div className="px-3 py-2 border-b border-gray-100 flex-shrink-0">
            <div className="relative">
              <select
                value={table}
                onChange={e => setTable(e.target.value)}
                className="w-full text-xs font-mono bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 pr-8 appearance-none text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-300"
              >
                {TABLES.map(t => (
                  <option key={t.key} value={t.key}>
                    {t.label}
                    {!(project.generated_scripts || {})[t.key] ? ' (not generated)' : ''}
                  </option>
                ))}
              </select>
              <ChevronDown className="w-3.5 h-3.5 text-gray-400 absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
            </div>
            {!hasScript && (
              <p className="text-xs text-amber-600 mt-1.5 flex items-center gap-1">
                <Code2 className="w-3 h-3" />
                Generate this script first for the best results.
              </p>
            )}
          </div>

          {/* Messages */}
          <div className="flex-1 overflow-y-auto px-3 py-3 flex flex-col gap-3 min-h-0">
            {loadingHistory && (
              <div className="flex justify-center py-8">
                <RefreshCw className="w-4 h-4 text-gray-400 animate-spin" />
              </div>
            )}

            {!loadingHistory && visibleMessages.length === 0 && (
              <div className="flex flex-col items-center justify-center h-full text-center gap-3 py-8">
                <div className="w-12 h-12 rounded-full bg-blue-50 flex items-center justify-center">
                  <MessageSquare className="w-5 h-5 text-blue-400" />
                </div>
                <div>
                  <p className="text-sm font-medium text-gray-700">No messages yet</p>
                  <p className="text-xs text-gray-400 mt-1 max-w-[240px]">
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
                      className="text-xs text-left text-blue-600 hover:text-blue-800 bg-blue-50 hover:bg-blue-100 px-3 py-2 rounded-lg transition-colors"
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
              <p className="text-xs text-red-600 text-center px-2">{error}</p>
            )}

            <div ref={bottomRef} />
          </div>

          {/* Input */}
          <div className="px-3 py-3 border-t border-gray-100 flex-shrink-0">
            <div className="flex items-end gap-2">
              <textarea
                ref={inputRef}
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Ask about the code or request changes… (Enter to send)"
                rows={2}
                className="flex-1 resize-none text-sm border border-gray-200 rounded-xl px-3 py-2.5 focus:outline-none focus:ring-2 focus:ring-blue-300 placeholder:text-gray-400 bg-gray-50"
              />
              <button
                onClick={handleSend}
                disabled={!input.trim() || sending}
                className={clsx(
                  'flex-shrink-0 w-9 h-9 rounded-xl flex items-center justify-center transition-colors',
                  input.trim() && !sending
                    ? 'bg-blue-600 text-white hover:bg-blue-700'
                    : 'bg-gray-100 text-gray-400 cursor-not-allowed',
                )}
              >
                {sending
                  ? <RefreshCw className="w-4 h-4 animate-spin" />
                  : <Send className="w-4 h-4" />
                }
              </button>
            </div>
            <p className="text-xs text-gray-400 mt-1.5 text-center">
              Shift+Enter for newline · Enter to send
            </p>
          </div>
        </div>
      )}
    </>
  )
}

function MessageBubble({ msg }: { msg: ChatMessage }) {
  const isUser = msg.role === 'user'

  return (
    <div className={clsx('flex gap-2', isUser ? 'flex-row-reverse' : 'flex-row')}>
      {/* Avatar */}
      <div className={clsx(
        'w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0 mt-0.5',
        isUser ? 'bg-blue-100' : 'bg-gray-100',
      )}>
        {isUser
          ? <User className="w-3.5 h-3.5 text-blue-600" />
          : <Bot className="w-3.5 h-3.5 text-gray-600" />
        }
      </div>

      {/* Bubble */}
      <div className={clsx(
        'max-w-[80%] rounded-2xl px-3.5 py-2.5 text-sm',
        isUser
          ? 'bg-blue-600 text-white rounded-tr-sm'
          : 'bg-gray-100 text-gray-800 rounded-tl-sm',
      )}>
        {msg.code_updated && (
          <div className="flex items-center gap-1.5 mb-2 text-xs text-green-700 bg-green-100 rounded-lg px-2.5 py-1.5 font-medium">
            <Code2 className="w-3 h-3" />
            Script updated — check the code preview above.
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
              className={clsx(
                'text-xs font-mono rounded-lg px-3 py-2.5 overflow-x-auto whitespace-pre',
                isUser ? 'bg-blue-700 text-blue-100' : 'bg-gray-800 text-gray-100',
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
      <div className="w-7 h-7 rounded-full bg-gray-100 flex items-center justify-center flex-shrink-0">
        <Bot className="w-3.5 h-3.5 text-gray-600" />
      </div>
      <div className="bg-gray-100 rounded-2xl rounded-tl-sm px-4 py-3">
        <div className="flex gap-1 items-center h-4">
          {[0, 1, 2].map(i => (
            <span
              key={i}
              className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce"
              style={{ animationDelay: `${i * 150}ms` }}
            />
          ))}
        </div>
      </div>
    </div>
  )
}
