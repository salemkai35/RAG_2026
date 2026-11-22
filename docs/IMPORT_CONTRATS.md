# Import du référentiel des contrats (CSV)

Si les informations de base des contrats existent déjà (tableur de suivi, ERP…), importez-les.
Elles deviennent la référence affichée par l'outil `fiche_contrat`, avec la source « import ».

```bash
docker compose run --rm -v "$PWD/docs/exemples:/import:ro" ingest contracts import /import/contrats.csv
```

- Séparateur `;` ou `,`, encodage UTF-8. Seule la colonne `reference` est obligatoire.
- Montants au format français (`12 500 000,00`) ou international (`12500000.00`).
- Dates au format `JJ/MM/AAAA` ou `AAAA-MM-JJ`.
- `taux_tva` en pourcentage (`19`).
- Chaque import est tracé dans la table `audit_log`.

| Colonne | Exemple |
|---|---|
| reference | IAP-EP-2023-014 |
| intitule | Réhabilitation du bloc pédagogique B |
| objet | Travaux de réhabilitation… |
| titulaire | ENTREPRISE EXEMPLE SARL |
| maitre_ouvrage | IAP |
| devise | DZD |
| montant_initial_ht | 125 000 000,00 |
| taux_tva | 19 |
| montant_initial_ttc | 148 750 000,00 |
| date_signature | 12/03/2023 |
| date_notification | 20/03/2023 |
| date_ods_demarrage | 02/04/2023 |
| delai_execution_jours | 240 |
| delai_execution_mois | |
| statut | en cours |

La référence doit être **identique au nom du sous-dossier** de `inbox/` pour que les
documents soient rattachés au bon contrat.
