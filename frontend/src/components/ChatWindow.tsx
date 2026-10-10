import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import Markdown from 'react-markdown'
import type { Message, ModelItem } from '../api'

interface ChatWindowProps {
  messages: Message[]
  loading: boolean
  streamingText: string
  draft: string
  onDraftChange: (value: string) => void
  onSend: (overrideMode?: 'chat' | 'note') => void
  onStop: () => void
  models: ModelItem[]
  selectedModel: string
  onSelectModel: (modelId: string) => void
  onRetry?: () => void
  error: string | null
  onClearError: () => void
}

export default function ChatWindow({
  messages,
  loading,
  streamingText,
  draft,
  onDraftChange,
  onSend,
  onStop,
  models,
  selectedModel,
  onSelectModel,
  onRetry,
  error,
  onClearError,
}: ChatWindowProps) {
  const bottomRef = useRef<HTMLDivElement>(null)
  const [composerMode, setComposerMode] = useState<'chat' | 'note'>('chat')

  // Maintient le scroll en bas pendant la génération et à chaque nouveau message
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamingText, loading])

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (!loading && draft.trim()) {
      onSend(composerMode)
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // Entrée envoie, Shift+Entrée insère un saut de ligne
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      if (!loading && draft.trim()) {
        onSend(composerMode)
      }
    }
  }

  const selectedModelObj = models.find((m) => m.id === selectedModel)

  return (
    <section className="chat">
      {/* Barre d'en-tête : Choix du modèle et mode d'écriture */}
      <header className="chat-header">
        <div className="model-selector-container">
          <label htmlFor="model-select" className="model-label">
            🤖 Modèle :
          </label>
          <select
            id="model-select"
            className="model-select"
            value={selectedModel}
            onChange={(e) => onSelectModel(e.target.value)}
            disabled={loading}
          >
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name} {m.is_default ? '(Défaut)' : ''}
              </option>
            ))}
          </select>
          {selectedModelObj && (
            <span className="model-desc" title={selectedModelObj.description}>
              {selectedModelObj.description}
            </span>
          )}
        </div>

        <div className="mode-toggle-group">
          <button
            type="button"
            className={`mode-btn ${composerMode === 'chat' ? 'active' : ''}`}
            onClick={() => setComposerMode('chat')}
            disabled={loading}
            title="Poser une question guidée au tuteur"
          >
            💬 Question
          </button>
          <button
            type="button"
            className={`mode-btn ${composerMode === 'note' ? 'active' : ''}`}
            onClick={() => setComposerMode('note')}
            disabled={loading}
            title="Enregistrer une note de révision privée (non envoyée au LLM)"
          >
            📝 Note
          </button>
        </div>
      </header>

      {/* Bannière d'erreur avec bouton Retry */}
      {error && (
        <div className="error-banner" role="alert">
          <span className="error-text">⚠️ {error}</span>
          <div className="error-actions">
            {onRetry && (
              <button type="button" className="retry-btn" onClick={onRetry}>
                🔄 Réessayer
              </button>
            )}
            <button
              type="button"
              className="close-btn"
              onClick={onClearError}
              aria-label="Fermer"
            >
              ×
            </button>
          </div>
        </div>
      )}

      {/* Liste des messages */}
      <div className="messages">
        {messages.length === 0 && !loading && (
          <div className="empty-prompt center muted">
            <h3>Bienvenue sur Study Buddy v2 👋</h3>
            <p>
              Pose ta première question sur Python ou enregistre une note de révision.
            </p>
          </div>
        )}

        {messages.map((m, i) => {
          if (m.role === 'system-notification') {
            return (
              <div key={i} className="notification">
                🔔 {m.content}
              </div>
            )
          }

          if (m.role === 'note') {
            return (
              <div key={i} className="bubble note">
                <div className="note-header">
                  <span className="note-badge">📌 Note personnelle</span>
                  <span className="note-hint">Sauvegardée en base · Exclue du LLM</span>
                </div>
                <div className="note-content">{m.content}</div>
              </div>
            )
          }

          return (
            <div key={i} className={`bubble ${m.role}`}>
              {m.role === 'assistant' ? (
                <>
                  <Markdown>{m.content}</Markdown>
                  {m.usage && (
                    <div className="token-usage-badge" title="Consommation de tokens">
                      🪙 {m.usage.total_tokens ?? m.usage.completion_tokens} tokens
                      {m.usage.prompt_tokens !== undefined && (
                        <span className="token-details">
                          {' '}
                          ({m.usage.prompt_tokens} entrée / {m.usage.completion_tokens} sortie)
                        </span>
                      )}
                    </div>
                  )}
                </>
              ) : (
                m.content
              )}
            </div>
          )
        })}

        {/* Affichage du streaming progressif en direct */}
        {loading && streamingText && (
          <div className="bubble assistant streaming">
            <Markdown>{streamingText}</Markdown>
            <span className="cursor-indicator">▍</span>
          </div>
        )}

        {/* Indicateur de frappe avant les premiers tokens */}
        {loading && !streamingText && (
          <div className="bubble assistant typing">
            <span className="typing-dot"></span>
            <span className="typing-dot"></span>
            <span className="typing-dot"></span>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Formulaire de saisie (Composer) */}
      <form className={`composer ${composerMode === 'note' ? 'note-composer' : ''}`} onSubmit={handleSubmit}>
        <div className="composer-input-wrapper">
          <textarea
            value={draft}
            onChange={(e) => onDraftChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              composerMode === 'note'
                ? 'Écris ta note personnelle de révision (stockée en base, jamais transmise au LLM)...'
                : 'Pose ta question sur Python (ex: Comment manipuler une boucle for ?)...'
            }
            rows={2}
            disabled={loading}
            autoFocus
          />
        </div>

        <div className="composer-buttons">
          {loading ? (
            <button
              type="button"
              className="stop-button"
              onClick={onStop}
              title="Arrêter la génération en cours"
            >
              ⏹ Arrêter
            </button>
          ) : (
            <>
              {composerMode === 'note' ? (
                <button
                  type="submit"
                  className="save-note-button"
                  disabled={!draft.trim()}
                >
                  💾 Sauvegarder la note
                </button>
              ) : (
                <>
                  <button
                    type="submit"
                    className="send-button"
                    disabled={!draft.trim()}
                  >
                    Envoyer
                  </button>
                  <button
                    type="button"
                    className="quick-note-button"
                    disabled={!draft.trim()}
                    onClick={() => onSend('note')}
                    title="Sauvegarder ce texte comme note personnelle sans appeler le LLM"
                  >
                    + Note
                  </button>
                </>
              )}
            </>
          )}
        </div>
      </form>
    </section>
  )
}
