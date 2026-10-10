import { useEffect, useRef, useState } from 'react'
import './App.css'
import {
  addNote,
  createConversation,
  getMessages,
  getModels,
  listConversations,
  streamChatMessage,
  type ConversationSummary,
  type Message,
  type ModelItem,
} from './api'
import ChatWindow from './components/ChatWindow'
import Sidebar from './components/Sidebar'

function formatErrorMessage(err: unknown): string {
  return err instanceof Error ? err.message : 'Une erreur inattendue est survenue.'
}

export default function App() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [activeId, setActiveId] = useState<number | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [draft, setDraft] = useState('')
  const [loading, setLoading] = useState(false)
  const [streamingText, setStreamingText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [lastFailedPrompt, setLastFailedPrompt] = useState<string | null>(null)

  // Modèles autorisés
  const [models, setModels] = useState<ModelItem[]>([])
  const [selectedModel, setSelectedModel] = useState<string>('')

  // Référence d'annulation pour le bouton Stop
  const abortControllerRef = useRef<AbortController | null>(null)
  // Accumulateur de streaming pour la fermeture
  const streamingTextRef = useRef<string>('')

  // 1. Initialisation au démarrage : liste des conversations et modèles autorisés
  useEffect(() => {
    // Chargement des conversations
    listConversations()
      .then((list) => {
        setConversations(list)
        if (list.length > 0) setActiveId(list[0].id)
      })
      .catch((err) => setError(formatErrorMessage(err)))

    // Chargement des modèles autorisés depuis le backend
    getModels()
      .then((res) => {
        setModels(res.models)
        setSelectedModel(res.default)
      })
      .catch((err) => setError(`Impossible de charger les modèles : ${formatErrorMessage(err)}`))
  }, [])

  // 2. Chargement des messages lors du changement de conversation
  useEffect(() => {
    if (activeId === null) return
    // Interrompre un éventuel stream en cours
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      abortControllerRef.current = null
    }
    setLoading(false)
    setStreamingText('')
    streamingTextRef.current = ''

    let cancelled = false
    getMessages(activeId)
      .then((list) => {
        if (!cancelled) setMessages(list)
      })
      .catch((err) => !cancelled && setError(formatErrorMessage(err)))

    return () => {
      cancelled = true
    }
  }, [activeId])

  function selectConversation(id: number) {
    if (loading || id === activeId) return
    setError(null)
    setDraft('')
    setMessages([])
    setActiveId(id)
  }

  async function handleNewConversation() {
    if (loading) return
    setError(null)
    try {
      const id = await createConversation()
      setConversations((list) => [
        { id, created_at: new Date().toISOString(), preview: null },
        ...list,
      ])
      setDraft('')
      setMessages([])
      setActiveId(id)
    } catch (err) {
      setError(formatErrorMessage(err))
    }
  }

  // Envoi d'un message (ou d'une note)
  async function handleSend(overrideMode?: 'chat' | 'note') {
    if (activeId === null) return
    const text = draft.trim()
    if (!text) return

    // Détection si c'est une note (soit via le mode, soit via la commande /note)
    const isNote = overrideMode === 'note' || text.startsWith('/note ')
    const content = isNote && text.startsWith('/note ') ? text.replace(/^\/note\s+/, '') : text

    if (isNote) {
      // --- Traitement du Rôle Custom 'note' ---
      try {
        setError(null)
        setDraft('')
        const savedNote = await addNote(activeId, content)
        setMessages((prev) => [...prev, savedNote])
      } catch (err) {
        setDraft(text)
        setError(formatErrorMessage(err))
      }
      return
    }

    // --- Traitement du Dialogue avec Streaming ---
    setError(null)
    setLastFailedPrompt(text)
    setDraft('')
    setStreamingText('')
    streamingTextRef.current = ''
    setLoading(true)

    // Affichage optimiste du message utilisateur
    const optimisticSeq = messages.length > 0 ? messages[messages.length - 1].seq + 1 : 1
    const optimisticUserMsg: Message = {
      seq: optimisticSeq,
      role: 'user',
      content: text,
      created_at: new Date().toISOString(),
    }
    setMessages((prev) => [...prev, optimisticUserMsg])

    const controller = new AbortController()
    abortControllerRef.current = controller

    try {
      await streamChatMessage(
        activeId,
        text,
        selectedModel,
        controller.signal,
        {
          onDelta: (chunk: string) => {
            streamingTextRef.current += chunk
            setStreamingText(streamingTextRef.current)
          },
          onDone: async ({ notification, usage }) => {
            const finalReply = streamingTextRef.current
            const assistantMsg: Message = {
              seq: optimisticSeq + 1,
              role: 'assistant',
              content: finalReply,
              created_at: new Date().toISOString(),
              usage,
            }

            const newMessages: Message[] = [assistantMsg]
            if (notification) {
              newMessages.push({
                seq: optimisticSeq + 2,
                role: 'system-notification',
                content: notification,
                created_at: new Date().toISOString(),
              })
            }

            setMessages((prev) => [...prev, ...newMessages])
            setStreamingText('')
            streamingTextRef.current = ''
            setLoading(false)
            abortControllerRef.current = null
            setLastFailedPrompt(null)

            // Mise à jour de l'aperçu si premier échange
            if (messages.length === 0) {
              const updatedList = await listConversations()
              setConversations(updatedList)
            }
          },
          onError: (errMsg: string) => {
            // En cas d'erreur reçue du flux SSE : rollback côté client
            setMessages((prev) => prev.filter((m) => m !== optimisticUserMsg))
            setDraft(text)
            setError(errMsg)
            setStreamingText('')
            streamingTextRef.current = ''
            setLoading(false)
            abortControllerRef.current = null
          },
        },
      )
    } catch (err: unknown) {
      if (controller.signal.aborted) {
        // Interrompu par l'utilisateur via le bouton Stop
        setMessages((prev) => prev.filter((m) => m !== optimisticUserMsg))
        setDraft(text)
        setError('Génération arrêtée. La réponse partielle n’a pas été enregistrée en base.')
      } else {
        // Erreur réseau ou 502
        setMessages((prev) => prev.filter((m) => m !== optimisticUserMsg))
        setDraft(text)
        setError(formatErrorMessage(err))
      }
      setStreamingText('')
      streamingTextRef.current = ''
      setLoading(false)
      abortControllerRef.current = null
    }
  }

  // Arrêt du streaming (Bouton Stop)
  function handleStop() {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      abortControllerRef.current = null
    }
  }

  // Ré-essai de la dernière requête échouée
  function handleRetry() {
    if (!lastFailedPrompt) return
    setDraft(lastFailedPrompt)
    setError(null)
  }

  return (
    <div className="app">
      <Sidebar
        conversations={conversations}
        activeId={activeId}
        onSelect={selectConversation}
        onNew={handleNewConversation}
      />
      <main className="main">
        {activeId === null ? (
          <div className="empty">
            <p>Aucune conversation ouverte.</p>
            <button className="new-button" onClick={handleNewConversation}>
              + Nouvelle conversation
            </button>
          </div>
        ) : (
          <ChatWindow
            messages={messages}
            loading={loading}
            streamingText={streamingText}
            draft={draft}
            onDraftChange={setDraft}
            onSend={handleSend}
            onStop={handleStop}
            models={models}
            selectedModel={selectedModel}
            onSelectModel={setSelectedModel}
            onRetry={lastFailedPrompt ? handleRetry : undefined}
            error={error}
            onClearError={() => setError(null)}
          />
        )}
      </main>
    </div>
  )
}
