import asyncio
import json
import os
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.db import SessionLocal, get_db
from database.models import Conversation, Message

load_dotenv()

# Configuration RodiumAI
BASE_URL = os.getenv("BASE_URL", "https://api.rodiumai.io/v1").rstrip("/")
RODIUMAI_URL = f"{BASE_URL}/chat/completions"
RODIUMAI_API_KEY = os.getenv("RODIUMAI_API_KEY")

if not RODIUMAI_API_KEY:
    raise RuntimeError(
        "RODIUMAI_API_KEY manquante. Définissez-la dans le fichier .env ou les variables d'environnement."
    )

# Configuration des modèles autorisés
# Liste configurable depuis .env : "google/gemini-2.5-flash-lite,google/gemini-3.8-flash,anthropic/claude-sonnet-4-5-20250929"
ALLOWED_MODELS_ENV = os.getenv(
    "ALLOWED_MODELS",
    "google/gemini-2.5-flash-lite,google/gemini-3.8-flash,anthropic/claude-sonnet-4-5-20250929",
)
ALLOWED_MODELS = [m.strip() for m in ALLOWED_MODELS_ENV.split(",") if m.strip()]
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", ALLOWED_MODELS[0] if ALLOWED_MODELS else "google/gemini-2.5-flash-lite")

MODEL_METADATA = {
    "google/gemini-2.5-flash-lite": {
        "name": "Gemini 2.5 Flash Lite",
        "description": "Ultra rapide, faible latence & économe en tokens",
    },
    "google/gemini-3.8-flash": {
        "name": "Gemini 3.8 Flash",
        "description": "Équilibré, idéal pour explications et raisonnement",
    },
    "anthropic/claude-sonnet-4-5-20250929": {
        "name": "Claude 3.5 Sonnet",
        "description": "Excellente analyse technique et code Python",
    },
}

PREVIEW_LENGTH = 60

# Rôles reconnus par l'API de complétion LLM
LLM_ROLES = {"user", "assistant"}

# Rôle système de notification (session 4)
NOTIFICATION_ROLE = "system-notification"
NOTIFICATION_EVERY = 10
NOTIFICATION_TEXT = "Notification système : Une dizaine de messages écrits."

# Rôle custom pour les étudiants : note personnelle de révision
CUSTOM_ROLE_NOTE = "note"

# Chargement du prompt système spécialisé
PROMPT_FILE_PATH = Path(__file__).parent / os.getenv("SYSTEM_PROMPT_PATH", "prompts/system.md")


def load_system_prompt() -> str:
    """Charge le prompt système depuis son fichier dédié, avec repli de secours."""
    if PROMPT_FILE_PATH.exists():
        try:
            return PROMPT_FILE_PATH.read_text(encoding="utf-8").strip()
        except Exception as e:
            print(f"Avertissement : Erreur lors de la lecture de {PROMPT_FILE_PATH}: {e}")
    return (
        "Tu es Study Buddy, un tuteur bienveillant spécialisé en programmation Python pour débutants. "
        "Guide l'étudiant avec méthode sans lui donner directement la solution brute."
    )


app = FastAPI(
    title="Study Buddy Chatbot v2",
    description="Backend Study Buddy v2 avec streaming SSE, choix du modèle et rôle custom 'note'.",
    version="2.0.0",
)

# Sécurité CORS (permet les appels locaux si front et back tournent sur des ports différents)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Schémas Pydantic ---

class ConversationResponse(BaseModel):
    conversation_id: int


class ConversationSummary(BaseModel):
    id: int
    created_at: datetime
    preview: str | None


class ModelItem(BaseModel):
    id: str
    name: str
    description: str
    is_default: bool


class ModelsResponse(BaseModel):
    models: list[ModelItem]
    default: str


class ChatRequest(BaseModel):
    conversation_id: int
    message: str
    model: str | None = None
    stream: bool = True


class ChatResponse(BaseModel):
    reply: str
    notification: str | None = None
    usage: dict | None = None


class CreateNoteRequest(BaseModel):
    content: str


class MessageResponse(BaseModel):
    seq: int
    role: str
    content: str
    created_at: datetime


# --- Fonctions utilitaires ---

def load_messages(db: Session, conversation_id: int) -> list[Message]:
    """Charge les messages d'une conversation par ordre chronologique (seq)."""
    if db.get(Conversation, conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation non trouvée.")
    return db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.seq)
    ).all()


def build_llm_history(rows: list[Message]) -> list[dict]:
    """
    Construit l'historique filtré et structuré envoyé au modèle de langage.

    Traitement explicite des rôles :
    - 'user' et 'assistant' : conservés car ce sont les rôles standards du dialogue.
    - 'note' : RÔLE CUSTOM EXPLICITEMENT FILTRÉ (non envoyé au LLM).
      Justification pédagogique et cybersécurité :
      1. Confidentialité : les notes personnelles de l'étudiant restent privées sur sa machine.
      2. Non-pollution du contexte : éviter de perturber le fil logique de guidage socratique.
      3. Économie de tokens : chaque token inutilement transmis augmente le coût et la latence.
    - 'system-notification' : EXPLICITEMENT FILTRÉ (informations d'UI internes à la plateforme).
    """
    history: list[dict] = []
    for m in rows:
        if m.role in LLM_ROLES:
            history.append({"role": m.role, "content": m.content})
        elif m.role == CUSTOM_ROLE_NOTE:
            # Traitement explicite : filtré pour le LLM
            continue
        elif m.role == NOTIFICATION_ROLE:
            # Traitement explicite : filtré pour le LLM
            continue
    return history


# --- Routes Conversations ---

@app.post("/conversations", status_code=201)
def create_conversation(db: Session = Depends(get_db)) -> ConversationResponse:
    """Crée une nouvelle conversation vierge."""
    conversation = Conversation()
    db.add(conversation)
    db.commit()
    return ConversationResponse(conversation_id=conversation.id)


@app.get("/conversations")
def list_conversations(db: Session = Depends(get_db)) -> list[ConversationSummary]:
    """Liste les conversations par ordre antéchronologique avec aperçu du premier message."""
    rows = db.execute(
        select(Conversation, Message.content)
        .outerjoin(Message, and_(Message.conversation_id == Conversation.id, Message.seq == 1))
        .order_by(Conversation.id.desc())
    ).all()
    return [
        ConversationSummary(
            id=conversation.id,
            created_at=conversation.created_at,
            preview=content[:PREVIEW_LENGTH] if content else None,
        )
        for conversation, content in rows
    ]


@app.get("/conversations/{conversation_id}/messages")
def list_messages(conversation_id: int, db: Session = Depends(get_db)) -> list[MessageResponse]:
    """Retourne la totalité des messages d'une conversation (incluant les notes et notifications)."""
    return [
        MessageResponse(seq=m.seq, role=m.role, content=m.content, created_at=m.created_at)
        for m in load_messages(db, conversation_id)
    ]


# --- Route Rôle Custom : Notes d'apprentissage ---

@app.post("/conversations/{conversation_id}/notes", status_code=201)
def add_student_note(
    conversation_id: int,
    req: CreateNoteRequest,
    db: Session = Depends(get_db),
) -> MessageResponse:
    """
    Enregistre une note personnelle de révision prise par l'étudiant.
    - Le rôle 'note' est persisté en base avec son numéro de séquence unique.
    - Il n'appelle pas le LLM et sera filtré de l'historique d'inférence.
    """
    clean_content = req.content.strip()
    if not clean_content:
        raise HTTPException(status_code=400, detail="Le contenu de la note ne peut pas être vide.")

    rows = load_messages(db, conversation_id)
    next_seq = rows[-1].seq + 1 if rows else 1

    note_msg = Message(
        conversation_id=conversation_id,
        seq=next_seq,
        role=CUSTOM_ROLE_NOTE,
        content=clean_content,
    )
    db.add(note_msg)
    try:
        db.commit()
        db.refresh(note_msg)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Conflit de séquence lors de l'enregistrement de la note."
        )

    return MessageResponse(
        seq=note_msg.seq,
        role=note_msg.role,
        content=note_msg.content,
        created_at=note_msg.created_at,
    )


# --- Route Choix du Modèle ---

@app.get("/models")
def get_allowed_models() -> ModelsResponse:
    """Expose la liste sécurisée des modèles autorisés par le serveur."""
    return ModelsResponse(
        models=[
            ModelItem(
                id=m,
                name=MODEL_METADATA.get(m, {}).get("name", m),
                description=MODEL_METADATA.get(m, {}).get("description", "Modèle IA standard"),
                is_default=(m == DEFAULT_MODEL),
            )
            for m in ALLOWED_MODELS
        ],
        default=DEFAULT_MODEL,
    )


# --- Route Chat avec Streaming et Non-Streaming ---

@app.post("/chat")
async def chat(req: ChatRequest, db: Session = Depends(get_db)):
    """
    Endpoint d'interaction avec le tuteur Study Buddy.
    - Valide strictement le modèle demandé (400 si non autorisé).
    - Supporte le streaming SSE par défaut (stream: true) ou la réponse JSON synchrone.
    - Sauvegarde atomique en base uniquement après la fin complète du flux.
    """
    # 1. Validation de l'existence de la conversation
    if db.get(Conversation, req.conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation non trouvée.")

    clean_message = req.message.strip()
    if not clean_message:
        raise HTTPException(status_code=400, detail="Le message ne peut pas être vide.")

    # 2. Validation stricte du modèle (Principe cybersécurité : 'Never trust client input')
    selected_model = req.model or DEFAULT_MODEL
    if selected_model not in ALLOWED_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"Modèle non autorisé : '{selected_model}'. Modèles acceptés : {ALLOWED_MODELS}",
        )

    # 3. Chargement de l'historique et préparation du prompt
    rows = load_messages(db, req.conversation_id)
    history = build_llm_history(rows)
    next_seq = rows[-1].seq + 1 if rows else 1

    system_prompt = load_system_prompt()
    user_payload = {"role": "user", "content": clean_message}
    llm_messages = [{"role": "system", "content": system_prompt}, *history, user_payload]

    # --- Mode Non-Streaming (repli de compatibilité) ---
    if not req.stream:
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                res = await client.post(
                    RODIUMAI_URL,
                    headers={"Authorization": f"Bearer {RODIUMAI_API_KEY}"},
                    json={
                        "model": selected_model,
                        "messages": llm_messages,
                        "max_tokens": 1024,
                        "stream": False,
                    },
                )
                res.raise_for_status()
                data = res.json()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="L'appel à l'API LLM a échoué.") from exc

        reply_text = data["choices"][0]["message"]["content"]
        usage_info = data.get("usage")

        db.add_all([
            Message(conversation_id=req.conversation_id, seq=next_seq, role="user", content=clean_message),
            Message(conversation_id=req.conversation_id, seq=next_seq + 1, role="assistant", content=reply_text),
        ])

        notif_text = None
        if (len(history) + 2) % NOTIFICATION_EVERY == 0:
            notif_text = NOTIFICATION_TEXT
            db.add(Message(
                conversation_id=req.conversation_id,
                seq=next_seq + 2,
                role=NOTIFICATION_ROLE,
                content=notif_text,
            ))

        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="La conversation a été modifiée en parallèle.")

        return ChatResponse(reply=reply_text, notification=notif_text, usage=usage_info)

    # --- Mode Streaming (SSE) ---
    async def sse_generator():
        accumulated_reply = ""
        collected_usage = None
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                async with client.stream(
                    "POST",
                    RODIUMAI_URL,
                    headers={"Authorization": f"Bearer {RODIUMAI_API_KEY}"},
                    json={
                        "model": selected_model,
                        "messages": llm_messages,
                        "max_tokens": 1024,
                        "stream": True,
                        "stream_options": {"include_usage": True},
                    },
                ) as upstream:
                    if upstream.status_code != 200:
                        err_text = await upstream.aread()
                        error_msg = f"Erreur de l'API LLM ({upstream.status_code}): {err_text.decode(errors='replace')}"
                        yield f"data: {json.dumps({'error': error_msg})}\n\n"
                        yield "data: [DONE]\n\n"
                        return

                    async for line in upstream.aiter_lines():
                        if not line:
                            continue
                        if line.startswith("data: "):
                            raw_data = line[6:].strip()
                            if raw_data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(raw_data)
                            except json.JSONDecodeError:
                                continue

                            # Extraction du delta de contenu
                            choices = chunk.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                delta_text = delta.get("content", "")
                                if delta_text:
                                    accumulated_reply += delta_text
                                    yield f"data: {json.dumps({'delta': delta_text})}\n\n"

                            # Extraction de la consommation de tokens
                            if "usage" in chunk and chunk["usage"]:
                                collected_usage = chunk["usage"]

            # Si aucune réponse n'a été produite
            if not accumulated_reply.strip():
                yield f"data: {json.dumps({'error': 'Le modèle a retourné une réponse vide.'})}\n\n"
                yield "data: [DONE]\n\n"
                return

            # Sauvegarde atomique en base : UNIQUEMENT une fois le flux achevé avec succès !
            with SessionLocal() as commit_db:
                # Vérification de cohérence du séquenceur
                fresh_rows = load_messages(commit_db, req.conversation_id)
                commit_seq = fresh_rows[-1].seq + 1 if fresh_rows else 1

                commit_db.add(Message(
                    conversation_id=req.conversation_id,
                    seq=commit_seq,
                    role="user",
                    content=clean_message,
                ))
                commit_db.add(Message(
                    conversation_id=req.conversation_id,
                    seq=commit_seq + 1,
                    role="assistant",
                    content=accumulated_reply,
                ))

                notif_triggered = None
                if (len(history) + 2) % NOTIFICATION_EVERY == 0:
                    notif_triggered = NOTIFICATION_TEXT
                    commit_db.add(Message(
                        conversation_id=req.conversation_id,
                        seq=commit_seq + 2,
                        role=NOTIFICATION_ROLE,
                        content=notif_triggered,
                    ))

                commit_db.commit()

            # Envoi des métadonnées finales
            finish_event = {
                "done": True,
                "notification": notif_triggered,
                "usage": collected_usage,
            }
            yield f"data: {json.dumps(finish_event)}\n\n"
            yield "data: [DONE]\n\n"

        except (asyncio.CancelledError, GeneratorExit):
            # L'utilisateur a cliqué sur 'Stop' ou fermé la connexion :
            # Comportement de sécurité : aucun enregistrement partiel en base pour éviter toute incohérence.
            return
        except IntegrityError:
            yield f"data: {json.dumps({'error': 'Conflit de concurrence lors de la sauvegarde.'})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as exc:
            # Erreur inattendue : transaction non validée
            yield f"data: {json.dumps({'error': f'Erreur réseau ou interne : {str(exc)}'})}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
