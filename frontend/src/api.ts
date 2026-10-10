// Thin typed wrappers around the FastAPI backend (proxied under /api by Vite).

export type Role = 'user' | 'assistant' | 'system-notification' | 'note'

export interface ConversationSummary {
  id: number
  created_at: string
  preview: string | null
}

export interface UsageInfo {
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
}

export interface Message {
  seq: number
  role: Role
  content: string
  created_at: string
  usage?: UsageInfo
}

export interface ModelItem {
  id: string
  name: string
  description: string
  is_default: boolean
}

export interface ModelsResponse {
  models: ModelItem[]
  default: string
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`/api${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    })
  } catch {
    throw new Error('Impossible de joindre le serveur.')
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    const detail = typeof body?.detail === 'string' ? body.detail : null
    throw new Error(detail ?? `Erreur ${response.status}`)
  }
  return response.json() as Promise<T>
}

export function listConversations(): Promise<ConversationSummary[]> {
  return request('/conversations')
}

export async function createConversation(): Promise<number> {
  const { conversation_id } = await request<{ conversation_id: number }>('/conversations', {
    method: 'POST',
  })
  return conversation_id
}

export function getMessages(conversationId: number): Promise<Message[]> {
  return request(`/conversations/${conversationId}/messages`)
}

export function getModels(): Promise<ModelsResponse> {
  return request('/models')
}

export function addNote(conversationId: number, content: string): Promise<Message> {
  return request(`/conversations/${conversationId}/notes`, {
    method: 'POST',
    body: JSON.stringify({ content }),
  })
}

export interface StreamChatCallbacks {
  onDelta: (chunk: string) => void
  onDone: (data: { notification?: string | null; usage?: UsageInfo }) => void
  onError: (error: string) => void
}

/**
 * Envoie un message utilisateur et lit le flux SSE avec fetch() + response.body.getReader()
 */
export async function streamChatMessage(
  conversationId: number,
  message: string,
  model: string,
  signal: AbortSignal | undefined,
  callbacks: StreamChatCallbacks,
): Promise<void> {
  let response: Response
  try {
    response = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        conversation_id: conversationId,
        message,
        model,
        stream: true,
      }),
      signal,
    })
  } catch (err: unknown) {
    if (signal?.aborted) {
      throw new Error('Génération interrompue par l’utilisateur.')
    }
    throw new Error('Impossible de joindre le serveur backend.')
  }

  if (!response.ok) {
    const body = await response.json().catch(() => null)
    const detail = typeof body?.detail === 'string' ? body.detail : null
    throw new Error(detail ?? `Erreur serveur (${response.status})`)
  }

  if (!response.body) {
    throw new Error('Le serveur n’a pas renvoyé de flux de données.')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      // Conserver la dernière ligne incomplète dans le buffer
      buffer = lines.pop() ?? ''

      for (const rawLine of lines) {
        const line = rawLine.trim()
        if (!line || !line.startsWith('data: ')) continue

        const payloadStr = line.slice(6).trim()
        if (payloadStr === '[DONE]') {
          continue
        }

        try {
          const parsed = JSON.parse(payloadStr)
          if (parsed.error) {
            callbacks.onError(parsed.error)
            return
          }
          if (parsed.delta) {
            callbacks.onDelta(parsed.delta)
          }
          if (parsed.done) {
            callbacks.onDone({
              notification: parsed.notification,
              usage: parsed.usage,
            })
          }
        } catch {
          // Ligne JSON tronquée ou non valide ignorée
        }
      }
    }
  } catch (err: unknown) {
    if (signal?.aborted) {
      throw new Error('Génération interrompue par l’utilisateur.')
    }
    throw err
  }
}
