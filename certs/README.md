# Autorités de certification internes

Déposez ici les certificats racine/intermédiaires de l'entreprise au format PEM
avec l'extension `.crt` (ex. `sonatrach-root-ca.crt`).

Ils sont intégrés aux images `open-webui` et `ingest` au moment du build
(`update-ca-certificates`), ce qui permet d'appeler les API internes des modèles
en HTTPS avec vérification TLS active (`AI_VERIFY_TLS=true`).

Les fichiers de ce dossier ne sont pas versionnés (voir `.gitignore`).
