import asyncio
import json
import os
import sys
from fastapi.testclient import TestClient

# Assurer l'accès aux modules locaux
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import app, build_llm_history, ALLOWED_MODELS
from database.models import Message

client = TestClient(app)

def test_full_workflow():
    print("=== [1] Test GET /models ===")
    res_models = client.get("/models")
    assert res_models.status_code == 200, f"Erreur GET /models: {res_models.text}"
    models_data = res_models.json()
    print("Modèles retournés :", [m["id"] for m in models_data["models"]])
    assert len(models_data["models"]) >= 2, "Doit exposer au moins 2 modèles"
    assert models_data["default"] in ALLOWED_MODELS

    print("\n=== [2] Test POST /conversations ===")
    res_conv = client.post("/conversations")
    assert res_conv.status_code == 201, f"Erreur création conv: {res_conv.text}"
    conv_id = res_conv.json()["conversation_id"]
    print(f"Conversation créée avec ID: {conv_id}")

    print("\n=== [3] Test Sécurité : Refus d'un modèle non autorisé (HTTP 400) ===")
    bad_req = {
        "conversation_id": conv_id,
        "message": "Bonjour",
        "model": "malicious-hacker-model-gpt-99",
    }
    res_bad = client.post("/chat", json=bad_req)
    assert res_bad.status_code == 400, f"Attendu 400, reçu {res_bad.status_code}: {res_bad.text}"
    print(f"Succès : Le backend a rejeté le faux modèle avec 400 : {res_bad.json()['detail']}")

    print("\n=== [4] Test Rôle Custom : Ajout d'une note étudiante ===")
    res_note = client.post(
        f"/conversations/{conv_id}/notes",
        json={"content": "Rappel de révision : bien penser à vérifier if __name__ == '__main__'"}
    )
    assert res_note.status_code == 201, f"Erreur note: {res_note.text}"
    note_data = res_note.json()
    assert note_data["role"] == "note"
    assert note_data["seq"] == 1
    print(f"Succès : Note enregistrée en base avec rôle='{note_data['role']}' et seq={note_data['seq']}")

    print("\n=== [5] Test Traitement explicite dans build_llm_history ===")
    test_rows = [
        Message(seq=1, role="note", content="Ceci est ma note perso"),
        Message(seq=2, role="user", content="Comment déclarer une liste ?"),
        Message(seq=3, role="assistant", content="En utilisant des crochets []"),
        Message(seq=4, role="system-notification", content="Notification système"),
    ]
    history = build_llm_history(test_rows)
    roles_in_history = [m["role"] for m in history]
    assert "note" not in roles_in_history, "Le rôle 'note' ne doit JAMAIS être envoyé au LLM !"
    assert "system-notification" not in roles_in_history, "Le rôle 'system-notification' ne doit pas être envoyé au LLM !"
    assert roles_in_history == ["user", "assistant"], f"Historique incorrect : {roles_in_history}"
    print("Succès : build_llm_history a filtré 'note' et 'system-notification' !")

    print("\n=== [6] Test Streaming SSE /chat ===")
    chat_payload = {
        "conversation_id": conv_id,
        "message": "Salut Study Buddy, comment créer une fonction en Python ?",
        "model": "google/gemini-2.5-flash-lite",
        "stream": True,
    }
    with client.stream("POST", "/chat", json=chat_payload) as stream_res:
        assert stream_res.status_code == 200, f"Stream status: {stream_res.status_code}"
        deltas = []
        events = []
        for line in stream_res.iter_lines():
            if line.startswith("data: "):
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    events.append("[DONE]")
                    break
                parsed = json.loads(data_str)
                events.append(parsed)
                if "delta" in parsed:
                    deltas.append(parsed["delta"])
        
        full_text = "".join(deltas)
        print(f"Texte reçu en streaming ({len(deltas)} chunks) : {full_text[:120]}...")
        assert len(deltas) > 0, "Aucun chunk reçu en streaming !"
        assert "[DONE]" in events, "Fin de flux [DONE] attendue !"

    print("\n=== [7] Test Vérification Base de Données après Stream ===")
    res_msgs = client.get(f"/conversations/{conv_id}/messages")
    assert res_msgs.status_code == 200
    msgs = res_msgs.json()
    print("Messages persistés en base :")
    for m in msgs:
        print(f" - seq {m['seq']}: [{m['role']}] {m['content'][:60]}...")
    
    # Doit contenir : seq 1 (note), seq 2 (user), seq 3 (assistant)
    assert len(msgs) == 3, f"Attendu 3 messages, trouvé {len(msgs)}"
    assert msgs[0]["role"] == "note"
    assert msgs[1]["role"] == "user"
    assert msgs[2]["role"] == "assistant"
    print("\n>>> TOUS LES TESTS DU BACKEND ONT RÉUSSI AVEC SUCCÈS ! <<<")

if __name__ == "__main__":
    test_full_workflow()
