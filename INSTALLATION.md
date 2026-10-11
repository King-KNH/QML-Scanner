# Installation du scanner QML — pas à pas (faisable depuis le téléphone)

Tout tourne gratuitement sur GitHub Actions, déclenché toutes les 15 minutes par cron-job.org (comme Shadow Retracement).

## 0. Avant de commencer
- **Régénère le token de ton bot** dans @BotFather (/mybots → ton bot → API Token → Revoke current token) : l'ancien était visible
  sur une capture. Le bot (nom, lien) reste le même. Écris-lui `/start` pour qu'il puisse t'écrire.
- Garde sous la main : le nouveau token du bot, ton `TELEGRAM_CHAT_ID` (le même que Shadow Retracement), ta clé Groq
  (la même), ta clé Twelve Data (la même, facultative) et ton identifiant d'application Deriv.
- **Ne mets jamais une clé dans un fichier ni dans un message.** Elles vont uniquement dans les « Secrets » GitHub.

## 1. Créer le dépôt et installer les fichiers
1. GitHub → **New repository** → nom `qml-scanner` → **Public** → Create.
2. **Add file → Upload files** : dépose le fichier `qml_scanner_v1.zip` (le zip tel quel, sans le décompresser) → Commit.
3. **Add file → Create new file** : nom `.github/workflows/installer.yml` (tape le chemin avec les `/`, GitHub crée les dossiers).
   Colle le contenu du bloc « installer.yml » ci-dessous → Commit.
4. Onglet **Actions → Installer → Run workflow**. Quand il est vert, le dépôt contient tout le projet.
5. Recrée de la même façon (Create new file) les deux autres fichiers de workflow, dont le texte est dans le dépôt, dossier `docs/workflows/` :
   `.github/workflows/scan.yml` et `.github/workflows/diagnose.yml` (ouvre-les dans `docs/workflows/`, copie, colle).
   (GitHub interdit à l'installateur de créer des workflows : c'est pour ça que ces fichiers se collent à la main.)

```yaml
name: Installer
on: workflow_dispatch
permissions:
  contents: write
jobs:
  install:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Décompresser et enregistrer
        run: |
          unzip -o qml_scanner_v1.zip -d _tmp
          tar -C _tmp/qml_scanner --exclude='./.github' -cf - . | tar -xf -
          rm -rf _tmp qml_scanner_v1.zip
          git config user.name "installer"
          git config user.email "installer@users.noreply.github.com"
          git add -A
          git commit -m "Installation du scanner QML" || echo "rien à enregistrer"
          git push
```

## 2. Les secrets
Dépôt → **Settings → Secrets and variables → Actions → New repository secret**. Crée exactement ces noms :

| Nom | Valeur |
|---|---|
| `TELEGRAM_BOT_TOKEN` | le token (régénéré) du bot QML |
| `TELEGRAM_CHAT_ID` | ton identifiant de conversation (le même que pour Shadow Retracement) |
| `GROQ_API_KEY` | la même clé Groq que Shadow Retracement |
| `TWELVEDATA_API_KEY` | la même clé Twelve Data (facultatif : secours forex / or / crypto) |
| `DERIV_APP_ID` | ton identifiant d'application Deriv (facultatif ; les données de marché sont publiques) |

## 3. Premier test : le diagnostic
**Actions → Diagnostic → Run workflow.** Le résultat arrive sur Telegram (et dans le journal du workflow). Il dit exactement :
quels points d'accès Deriv répondent (et l'erreur précise des autres), combien d'actifs sont retrouvés, si les bougies arrivent,
et si Twelve Data, Telegram et Groq fonctionnent. **Envoie-moi ce message** : c'est ce qui me permet de corriger quoi que ce soit.

## 4. Premier scan
**Actions → Scan QML → Run workflow.** Le premier lancement télécharge l'historique (35 jours de M5 et 420 jours de H1 par actif) :
il peut en rester une partie pour les lancements suivants (« deferred » dans le journal), c'est normal. Les alertes ne commencent
qu'avec des données suffisantes.

## 5. Le déclenchement toutes les 15 minutes (cron-job.org)
1. GitHub → Settings → Developer settings → **Fine-grained tokens** → nouveau token, dépôt `qml-scanner` seulement,
   permission **Actions : Read and write**.
2. cron-job.org → nouveau job :
   - URL : `https://api.github.com/repos/TON_COMPTE/qml-scanner/actions/workflows/scan.yml/dispatches`
   - Méthode : **POST** ; corps : `{"ref":"main"}`
   - En-têtes : `Authorization: Bearer TON_TOKEN`, `Accept: application/vnd.github+json`, `Content-Type: application/json`
   - Fréquence : toutes les 15 minutes.
   (Même principe que ton job de Shadow Retracement.)

## 6. Utilisation
Le bot épingle la liste des opportunités (classées par catégorie puis par actif) et t'envoie une alerte à chaque étape
(nouvelle, à l'approche, dans la zone, de retour). Tu lui écris pour supprimer, ajuster ou proposer un setup : voir `COMMANDES.md`.
**Il lit tes messages à chaque passage (toutes les 15 minutes)** : une réponse peut donc mettre jusqu'à 15 minutes.

## 7. En cas de problème
- Aucune alerte : lance **Diagnostic** et envoie-moi le message.
- « Actifs introuvables chez Deriv » : le nom de l'actif ne correspond pas à celui de Deriv ; envoie-moi la liste affichée.
- Erreur Cloudflare 520 ou accès bloqué : le diagnostic montre quel point d'accès est bloqué ; envoie-moi le message.
- Journal détaillé : Actions → le dernier « Scan QML » → étape « Scan, messages Telegram, alertes ».

## 8. Confidentialité (dépôt public)
La mémoire du scanner (historique de prix, opportunités, tes suppressions / ajustements / raisons) est stockée dans la « release »
`qml-data` du dépôt public : elle est visible de tous. Les clés restent protégées dans les Secrets. Si ça te gêne, passe le dépôt
en privé (les minutes gratuites sont alors limitées : espacer les scans).

## 9. L'indicateur TradingView
`indicateur/KING_QML.pine` : colle son contenu dans l'éditeur Pine pour remplacer l'ancienne version. Il applique les mêmes règles
que le scanner.
