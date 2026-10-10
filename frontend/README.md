# Study Buddy v2 - Frontend

Interface React 19 + TypeScript (Vite) pour le chatbot Study Buddy v2.

- **Auteur** : Kokou Olivier FIABI (`lynxxoven`)
- **Backend associé** : `../backend` (FastAPI + SSE Streaming)

---

## Fonctionnalités de l'interface

- **Lecture du flux en streaming** : Consommation de l'endpoint SSE `/api/chat` avec `fetch()` et `response.body.getReader()`, affichage du texte en temps réel avec curseur animé.
- **Choix dynamique du modèle** : Sélecteur déroulant alimenté par l'endpoint `GET /api/models` (Gemini 2.5 Flash Lite, Gemini 3.8 Flash, Claude 3.5 Sonnet).
- **Rôle custom « note »** :
  - Affichage des notes de révision avec style visuel distinct (carte ambrée avec icône 📌 et badge explicatif).
  - Possibilité pour l'étudiant d'enregistrer des notes soit via le bouton mode « 📝 Note », soit via le bouton rapide « + Note », soit avec la commande `/note <texte>`.
  - Les notes sont sauvegardées en base et jamais envoyées au LLM.
- **Bouton Stop (Annulation)** : Possibilité d'interrompre la génération en cours via un `AbortController` (la réponse partielle est rejetée pour préserver la cohérence de la base).
- **Bouton Réessayer (Retry)** : En cas d'erreur de connexion ou de rejet serveur, un bouton « 🔄 Réessayer » permet de renvoyer immédiatement la requête sans ressaisie.
- **Affichage des tokens consommés** : Badge discret `🪙 X tokens` sous chaque réponse de l'assistant.
- **Rendu Markdown complet** : Formatage du code et des listes via `react-markdown`.

---

## Lancement en local

1. S'assurer que le backend tourne sur le port `8000`.
2. Installer les dépendances :
   ```bash
   npm install
   ```
3. Lancer le serveur de développement :
   ```bash
   npm run dev
   ```
4. Ouvrir `http://localhost:5173`.
