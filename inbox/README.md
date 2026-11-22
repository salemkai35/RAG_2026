# Dossier de dépôt des documents

Un sous-dossier par contrat, nommé avec la **référence du contrat** :

```
inbox/
├── IAP-EP-2023-014/
│   ├── Contrat IAP-EP-2023-014.pdf
│   ├── Avenant n°1.pdf
│   ├── Avenant n°2.docx
│   ├── ODS 01 demarrage.pdf
│   ├── PV reception provisoire.pdf
│   ├── BPU.xlsx
│   └── manifest.csv          (optionnel)
└── IAP-EP-2024-002/
    └── ...
```

Le type de chaque document (contrat, avenant n°, ordre de service, PV de
réception, bordereau des prix, caution…) est déduit du nom de fichier.
Pour forcer une valeur, ajoutez un `manifest.csv` (séparateur `;`) :

```
fichier;type_document;numero_ordre;date_document;remplace
Avenant 2 signe.pdf;avenant;2;15/03/2024;Avenant 2 projet.pdf
```

Types reconnus : `contrat`, `avenant`, `ordre_service`, `pv_reception_provisoire`,
`pv_reception_definitive`, `levee_reserves`, `mainlevee_garantie`, `garantie`,
`bordereau_prix`, `cahier_charges`, `annexe`, `autre`.

Ingestion : `docker compose run --rm ingest ingest /inbox`
(les fichiers déjà indexés — même empreinte SHA-256 — sont ignorés).
