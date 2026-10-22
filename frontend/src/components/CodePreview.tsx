import { Light as SyntaxHighlighter } from 'react-syntax-highlighter'
import python from 'react-syntax-highlighter/dist/esm/languages/hljs/python'
import { githubGist } from 'react-syntax-highlighter/dist/esm/styles/hljs'
import { Copy, Check } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@/components/ui/button'

SyntaxHighlighter.registerLanguage('python', python)

interface Props {
  code: string
}

export default function CodePreview({ code }: Props) {
  const [copied, setCopied] = useState(false)

  const copy = async () => {
    await navigator.clipboard.writeText(code)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="relative border border-border rounded-xl overflow-hidden bg-card">
      <div className="flex items-center justify-between px-4 py-2 bg-muted border-b border-border">
        <span className="text-xs font-medium text-muted-foreground">Generated ETL Script (Python)</span>
        <Button
          variant="ghost"
          size="sm"
          onClick={copy}
          className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground h-auto py-1 px-2"
        >
          {copied ? <Check className="w-3.5 h-3.5 text-green-500" /> : <Copy className="w-3.5 h-3.5" />}
          {copied ? 'Copied!' : 'Copy'}
        </Button>
      </div>
      <SyntaxHighlighter
        language="python"
        style={githubGist}
        customStyle={{ margin: 0, maxHeight: '480px', fontSize: '0.8rem' }}
      >
        {code}
      </SyntaxHighlighter>
    </div>
  )
}
