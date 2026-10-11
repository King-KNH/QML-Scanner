# Commandes que tu peux écrire au bot (en français, texte libre)

Chaque opportunité a un **ID court** (ex. `#A3F7`) affiché dans la liste et dans les alertes.

| Pour… | Écris… |
|---|---|
| Supprimer une opportunité invalide | `supprime A3F7` · `enlève XAUUSD H1 vente car en range` · `supprime tout GBPUSD` |
| Annuler une suppression | `rétablis A3F7` · `liste des supprimées` |
| Corriger zone / ligne QML | `ajuste A3F7 zone 4190 4199 ligne 4192` |
| Proposer ton setup | `ajoute XAUUSD H1 vente zone 4190 4199 ligne 4192 tête 4221 support 4165 cassure 4150` |
| Voir ce que le robot a appris | `bilan` |
| Activer une suggestion | `applique 1` |
| Voir / annuler les réglages appris | `réglages` · `réinitialise les réglages` |

- Pour un setup proposé : **zone ou ligne obligatoire** ; tête, support, cassure sont facultatifs mais aident le robot à comprendre
  pourquoi il ne l'avait pas vu. Timeframes possibles : M15, M30, M45, H1, H2, H4, H8, D1.
- S'il y a plusieurs correspondances, le bot ne fait rien et te demande laquelle (donne l'ID).
- « Volatility 75 (1s) » désigne la version 1s ; « Volatility 75 » la version 2s.
- Les messages libres (« vire le setup de l'or ») sont compris par l'IA (Groq), qui utilise ces mêmes commandes.
  L'IA n'invente jamais de niveau et n'agit que sur ta demande explicite.
- Rien n'est jamais changé dans les règles sans ton accord : le bilan propose des suggestions numérotées, seul « applique N » les active.
