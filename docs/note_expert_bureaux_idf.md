# Note technique — Scorer Bureaux IDF
## Pour validation expert avant mise en production

**Date** : Avril 2026
**Projet** : Outil de screening d'opportunités d'acquisition bureaux en IDF
**Objet** : Validation des hypothèses de calcul et des filtres de données

---

## 1. Ce que fait l'outil

L'outil produit un fichier JSON (`bureaux_adresses.json`) contenant toutes les transactions DVF de locaux commerciaux en IDF, enrichies avec :
- Des estimations de loyer (source CBRE + contrôle par cap rate)
- Des hypothèses de vacance, franchise et charges par zone
- Des rendements (brut, économique, net-net)
- Des coordonnées GPS pour scoring par rayon géographique

**Usage** : pré-screening de deals. Pas un outil de valorisation — pas de DCF, pas d'audit ESG. Le scoring identifie les opportunités à creuser, l'expert décide.

---

## 2. Sources de données

| Source | Donnée | Date | Accès |
|--------|--------|------|-------|
| DVF (data.gouv.fr) | Transactions de vente locaux commerciaux | Glissant 5 ans | Open data, API |
| CBRE | Loyers bureaux par secteur IDF (83 secteurs) | Janvier 2026 | Publication gratuite |
| Cushman & Wakefield | Submarkets IDF : vacance marché, loyers prime/2de main | T4 2025 | MarketBeat gratuit |
| Immostat | Taux d'accompagnement (franchise) | T3 2025 | Retraité pour petites surfaces |
| INSEE | Indices ILC, ILAT, ICC | Trimestriel | API SDMX |

---

## 3. Chaîne de calcul par transaction

```
ENTRÉE (DVF)
  prix_total         = prix de vente observé
  surface_m2         = surface réelle bâtie
  prix_m2            = prix_total / surface

LOYER FACIAL (source primaire = CBRE)
  Si CBRE disponible pour le secteur :
    loyer_facial_m2_an = (CBRE usage min + CBRE usage max) / 2
  Sinon (fallback) :
    loyer_facial_m2_an = prix_m2 × yield_secondaire

LOYER ÉCONOMIQUE
  loyer_eco = loyer_facial × (1 - vacance_bp) × (1 - franchise_mois / 72)

LOYER NET PNR
  loyer_net_pnr = loyer_eco × (1 - charges_pnr)

RENDEMENTS
  brut     = loyer_facial × surface / prix_total
  éco      = loyer_eco × surface / prix_total
  net-net  = loyer_net_pnr × surface / (prix_total × 1.065)
                                        ↑ frais d'acquisition 6,5%
```

---

## 4. Filtres appliqués aux données DVF

### 4.1 Pipeline commercial (toutes transactions)

| Filtre | Valeur | Raison |
|--------|--------|--------|
| Nature mutation | Vente uniquement | Exclut VEFA |
| Type local | Contient "ocal" | Local commercial DVF |
| Surface | >= 9 m² | Exclut micro-lots |
| Prix/m² | 500 - 30 000 € | Exclut aberrations extrêmes |
| Agrégation | Single-lot uniquement | 1 lot par mutation |
| Trimming | MAD × 3.5 par dept × segment | Exclut outliers statistiques |

### 4.2 Export bureaux (filtre additionnel)

| Filtre | Valeur | Raison |
|--------|--------|--------|
| Surface | >= 100 m² | < 100m² = résidentiel/retail |
| Profil | "bureau" uniquement | Exclut retail_probable et activité_probable |
| Prix/m² Paris (75) | >= 4 000 € | Exclut ventes intra-groupe, judiciaires |
| Prix/m² 92 | >= 2 500 € | Idem |
| Prix/m² 93/94/autres | >= 1 500 € | Idem |
| Prix/m² max | <= 18 000 € | Exclut retail luxe |

### 4.3 Heuristique profil bureau vs retail

```
Si surface < 50m²                     → retail_probable (exclu)
Si surface < 80m² ET prix/m² > 8000€  → retail_probable (exclu)
Si surface > 5000m²                    → activite_probable (exclu)
Sinon                                  → bureau (gardé)
```

**Question expert** : Ces seuils sont-ils pertinents ? Faut-il un seuil surface différent pour Paris vs banlieue ? Un bureau pied d'immeuble à Paris peut-il faire 60-80m² légitimement ?

---

## 5. Grilles d'hypothèses — à valider

### 5.1 Yields secondaires (Q1 2026)

Pour surface < 700m², une prime de +25bp est ajoutée (liquidité moindre, vacance binaire).

| Zone | Yield sec. base | Avec prime <700m² |
|------|----------------|-------------------|
| Paris QCA (1er, 2e, 8e, 9e, 16e, 17e) | 6,25% | 6,50% |
| Paris Centre Ouest (5e, 6e, 7e, 15e) | 7,00% | 7,25% |
| Paris Sud (12e, 13e, 14e) | 7,00% | 7,25% |
| Paris Nord-Est (3e, 4e, 10e, 11e, 18e-20e) | 8,75% | 9,00% |
| La Défense | 7,00% | 7,25% |
| Neuilly-Levallois | 6,75% | 7,00% |
| Boucle Sud (Issy, Boulogne, Meudon...) | 7,50% | 7,75% |
| Péri-Défense (Nanterre, Rueil, Suresnes...) | 8,00% | 8,25% |
| Boucle Nord (Clichy, Asnières, Gennevilliers) | 8,75% | 9,00% |
| 1re Couronne Nord (Saint-Denis, Aubervilliers...) | 9,00% | 9,25% |
| 1re Couronne Est (Montreuil, Vincennes...) | 8,00% | 8,25% |
| 1re Couronne Sud (Ivry, Villejuif, Cachan...) | 7,75% | 8,00% |
| 2e Couronne | 9,00-9,50% | 9,25-9,75% |

**Questions expert** :
1. Ces yields sont-ils calibrés pour du **pied d'immeuble 300-700m² secondaire** ou pour des immeubles entiers ?
2. Faut-il ajouter une prime supplémentaire pour du pied d'immeuble mono-locataire ?
3. Les yields Boucle Sud (7,50%) sont-ils trop bas vs marché actuel (certains brokers annoncent 7,75-8,25%) ?

---

### 5.2 Vacance frictionnelle BP

Hypothèse : actif moyen en repositionnement, pas prime déjà loué.

| Zone | Vacance BP | Vacance marché (Cushman) | Ratio |
|------|-----------|------------------------|-------|
| Paris QCA | 6% | 5,3% | 1,1× |
| Paris Centre Ouest | 8% | 10,2% | 0,8× |
| Paris Sud | 8% | 7,5% | 1,1× |
| Paris Nord-Est | 11% | 12,6% | 0,9× |
| La Défense | 15% | 15,5% | 1,0× |
| Neuilly-Levallois | 8% | 11,3% | 0,7× |
| Boucle Sud | 12% | 14,9% | 0,8× |
| Péri-Défense | 13% | 23,6% | 0,6× |
| 1re Couronne Nord | 20% | 25,0% | 0,8× |
| 1re Couronne Est | 13% | 10,4% | 1,3× |
| 1re Couronne Sud | 11% | 16,4% | 0,7× |

**Questions expert** :
1. Le ratio vacance BP / vacance marché est-il cohérent ? Il varie de 0,6× à 1,3× — est-ce normal ou faut-il harmoniser ?
2. Pour un actif **vacant à l'acquisition** (pas déjà loué), faut-il prendre la vacance marché directe au lieu de la vacance BP ?
3. Péri-Défense à 13% BP vs 23,6% marché — est-ce trop optimiste ?

---

### 5.3 Franchise locative

Hypothèse : bail 6 ans ferme, petite surface 300-700m².

| Zone | Franchise (mois) | Décote loyer |
|------|-----------------|-------------|
| Paris QCA | 5 | 6,9% |
| Paris Centre Ouest | 7 | 9,7% |
| Paris Sud | 7 | 9,7% |
| Paris Nord-Est | 9 | 12,5% |
| La Défense | 15 | 20,8% |
| Neuilly-Levallois | 6 | 8,3% |
| Boucle Sud | 7 | 9,7% |
| Péri-Défense | 12 | 16,7% |
| 1re Couronne Nord | 15 | 20,8% |
| 1re Couronne Est | 10 | 13,9% |
| 1re Couronne Sud | 9 | 12,5% |

**Questions expert** :
1. Ces durées sont retraitées depuis Immostat T3 2025 (grandes surfaces ≥ 1000m²) pour des petites surfaces 300-700m². Le retraitement est-il pertinent ?
2. Faut-il différencier bail 3/6/9 avec break à 3 ans (fréquent < 1000m²) vs 6 ans ferme ?
3. Boucle Sud à 7 mois — un broker local dirait plutôt 8-10 mois ?

---

### 5.4 Charges PNR (Provisions Non Récupérables)

Inclut : charges propriétaire, gestion (2-4%), assurance PNO, taxe foncière non refacturable, gros entretien (art. 606).

| Zone | PNR |
|------|-----|
| Paris QCA / Neuilly | 8% |
| Paris hors QCA / Boucle Sud | 9% |
| La Défense / Péri-Défense | 10% |
| 1re Couronne Est/Sud | 10-11% |
| 1re Couronne Nord / 2e Couronne | 12% |

**Questions expert** :
1. 8-12% de PNR est-il réaliste pour du petit bureau secondaire ?
2. Faut-il distinguer PNR pour immeuble mono-locataire vs multi-locataire ?

---

## 6. Contrôles qualité intégrés

### 6.1 Cross-check CBRE vs cap rate
Chaque transaction a un flag `ECART_CAPRATE_CBRE` si l'écart entre le loyer déduit du cap rate et le loyer CBRE dépasse 15%.

**Lecture** : un écart > 15% signifie que le prix DVF n'est pas cohérent avec le loyer de marché. Causes possibles : vente intra-groupe, composante non-bureau dans le prix, asset exceptionnel.

### 6.2 Pondération temporelle
Half-life de 9 mois : une transaction de 6 mois pèse 65%, une de 18 mois pèse 25%.

**Question expert** : en marché de repricing (2023-2026), faut-il raccourcir à 6 mois ?

### 6.3 Profil bureau vs retail
Heuristique par surface. Limites connues : un bureau pied d'immeuble 70m² est classé retail, un restaurant 200m² est classé bureau.

---

## 7. Limites connues de l'outil

| Limite | Impact | Contournement |
|--------|--------|--------------|
| DVF mélange bureaux/commerces/activité | Bruit dans les prix/m² | Filtres surface + prix + profil |
| Pas de distinction étage vs RDC | Un RDC retail peut être classé bureau | Croisement avec cadastre/OSM à terme |
| Pas de DPE / ESG | Risque stranded asset non évalué | Due diligence bien par bien |
| CBRE = loyers à l'offre | Décote 5-15% vs loyer conclu | À appliquer manuellement si besoin |
| Franchise Immostat = grandes surfaces | Retraitement imparfait pour 300-700m² | Validation broker local |
| Pas de CapEx d'entrée | Sous-estime le coût total d'acquisition | Estimation bien par bien |

---

## 8. Format de sortie JSON

```json
{
  "meta": {
    "date": "2026-04-16",
    "nb_transactions": 2500,
    "nb_communes": 55,
    "methode": "Loyer primaire = CBRE. Cap rate = contrôle. Flag si écart >15%."
  },
  "communes": {
    "BOULOGNE BILLANCOURT": {
      "stats": { "nb_tx": 45, "prix_m2_median": 6500, "prix_m2_q25": 5200, "prix_m2_q75": 8100 },
      "loyer": { "facial_median_m2_an": 310, "eco_median_m2_an": 246, "net_pnr_median_m2_an": 224 },
      "rendement": { "eco_median_pct": 3.63, "net_net_median_pct": 2.98 },
      "risque": { "vacance_marche_pct": 14.9, "vacance_bp_pct": 12, "franchise_mois": 7 }
    }
  },
  "transactions": [
    {
      "lat": 48.831, "lng": 2.238,
      "commune": "BOULOGNE BILLANCOURT",
      "rue": "RUE DE SILLY", "numero": "12",
      "date_vente": "2024-06-15",
      "surface_m2": 120.5,
      "prix_m2": 7054,
      "loyer_facial_m2_an": 310,
      "loyer_eco_m2_an": 246,
      "loyer_net_pnr_m2_an": 224,
      "source_loyer": "cbre",
      "loyer_caprate_m2_an": 532,
      "ecart_caprate_cbre_pct": 71.6,
      "flag": "ECART_CAPRATE_CBRE",
      "rendement_brut_pct": 4.37,
      "rendement_eco_pct": 3.63,
      "rendement_net_net_pct": 2.98
    }
  ]
}
```

---

## 9. Résumé des questions pour l'expert

### Yields
1. Yields calibrés pied d'immeuble 300-700m² ou immeubles entiers ?
2. Prime supplémentaire mono-locataire ?
3. Boucle Sud 7,50% — trop bas ?

### Vacance
4. Ratio vacance BP / marché (0,6× à 1,3×) — cohérent ?
5. Actif vacant à l'achat = vacance marché directe ?
6. Péri-Défense 13% vs marché 23,6% — trop optimiste ?

### Franchise
7. Retraitement Immostat grandes surfaces → petites surfaces pertinent ?
8. Bail 3/6/9 avec break vs 6 ans ferme — impact sur le calcul ?
9. Boucle Sud 7 mois — réaliste ou trop court ?

### Filtres DVF
10. Plancher prix/m² Paris 4000€, 92 2500€ — adapté ?
11. Heuristique retail < 80m² — seuil correct ?
12. Half-life 9 mois — raccourcir à 6 en marché baissier ?

### Charges
13. PNR 8-12% — réaliste pour petit bureau secondaire ?
14. Distinction mono vs multi-locataire ?

---

*Document généré le 16 avril 2026 — Projet DVFMAJ*
