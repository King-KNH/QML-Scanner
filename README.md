# qml_scanner — scanner QML 24h/24 pour les actifs Deriv

Logique de détection (identique à l'indicateur KING_QML), données Deriv, alertes et conversation Telegram, agent Groq.
**Installation : voir `INSTALLATION.md`. Commandes : voir `COMMANDES.md`.**

## Modules
- `qml_core.py` détection QML · `candles.py` timeframes (M15…D1, M45 inclus) · `engine.py` statuts · `selection.py` regroupement multi-timeframe, règle Boom/Crash, corrélations
- `notify.py` événements sans doublon · `dismiss.py` / `overrides.py` / `manual.py` tes suppressions, ajustements, setups · `learning.py` étude et suggestions
- `store.py` mémoire (historique M5 + H1, état) · `sources.py` Deriv (WebSocket public) + Twelve Data · `datalayer.py` mise à jour de l'historique
- `telegram_io.py` · `agent.py` (Groq) · `runner.py` un passage complet · `diagnose.py` test de connexion · `report.py` messages · `config.py` actifs

## Tests
`python3 -m unittest discover -s tests` (212 tests). Les connexions réelles (Deriv, Telegram, Groq) ne peuvent pas être testées hors de GitHub : elles sont simulées dans les tests, et le workflow « Diagnostic » les vérifie en vrai.

## Règles de détection ajoutées dans cette version
- Cassure de la ligne QML par le CORPS d'une bougie : la tête doit avoir au moins une clôture au-delà du niveau de l'épaule gauche
  (une simple mèche ne valide plus). Réglable : `body_break`, `body_min`. Même règle dans l'indicateur Pine (KING_QML, version de travail).
- Cycle de vie : si le prix s'éloigne (une bougie clôture au-delà du LL pour une vente / du HH pour un achat) et n'est plus près de la ligne,
  l'opportunité est effacée de la liste SANS message et gardée en mémoire ; elle revient (message « 🔁 de retour ») quand le prix
  revient à l'approche de la ligne. Elle ne disparaît définitivement que si elle est invalidée ou expirée.
- Cassure de structure (LL pour une vente, HH pour un achat) également validée par le CORPS d'une bougie (clôture au-delà du support).
- Type de zone d'entrée : MPL, Order Block ou Zone QML (rectangle entre la ligne QML et la tête) ; `mode` = mpl / ob / qmlz / both / all.
- Timeframes scannés : M15, M30, M45, H1, H2, H4, H8, D1 (le M5 n'est qu'une donnée de base, pas scanné).
