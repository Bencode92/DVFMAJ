"""
Charge les CSV DVF bruts (Capital_*.csv), filtre/agrège les mutations,
calcule prix/m², buckets, poids temporels, trimming surface + prix ajusté.
Produit la table VenteDATA.
"""

import polars as pl
from pathlib import Path
import time

# ============================
# Paramètres
# ============================
AS_OF_DATE = pl.date(2025, 10, 31)
HALF_LIFE_MONTHS = 12
HALF_LIFE_DAYS = 30.4375 * HALF_LIFE_MONTHS
BETA_SURFACE = -0.20
Q_LOW = 0.10
Q_HIGH = 0.90

DATA_DIR = Path(__file__).parent.parent / "data"

USE_COLS = [
    "id_mutation", "date_mutation", "nature_mutation", "valeur_fonciere",
    "type_local", "surface_reelle_bati", "nombre_pieces_principales",
    "nom_commune", "code_departement", "code_commune",
]


def load_and_combine(data_dir=DATA_DIR):
    """Charge tous les CSV Capital_*.csv et les combine."""
    files = sorted(data_dir.glob("Capital_*.csv"))
    if not files:
        raise FileNotFoundError(f"Aucun fichier Capital_*.csv dans {data_dir}")

    dfs = []
    for f in files:
        df = pl.read_csv(
            f,
            columns=USE_COLS,
            schema_overrides={
                "id_mutation": pl.Utf8,
                "nature_mutation": pl.Utf8,
                "valeur_fonciere": pl.Float64,
                "type_local": pl.Utf8,
                "surface_reelle_bati": pl.Float64,
                "nombre_pieces_principales": pl.Int64,
                "nom_commune": pl.Utf8,
                "code_departement": pl.Utf8,
                "code_commune": pl.Utf8,
            },
            try_parse_dates=True,
            ignore_errors=True,
        )
        dfs.append(df)
        print(f"  {f.name}: {df.shape[0]:,} lignes")
    return pl.concat(dfs)


def filter_base(df):
    """Filtre: Ventes d'appartements, surface >= 9, date <= AsOfDate."""
    return df.filter(
        (pl.col("nature_mutation") == "Vente")
        & (pl.col("type_local") == "Appartement")
        & (pl.col("surface_reelle_bati").is_not_null())
        & (pl.col("surface_reelle_bati") >= 9)
        & (pl.col("date_mutation").is_not_null())
        & (pl.col("date_mutation") <= AS_OF_DATE)
    )


def aggregate_mutations(df):
    """Agrège par id_mutation (1 ligne = 1 mutation)."""
    agg = df.group_by("id_mutation").agg(
        pl.len().alias("n_apparts"),
        pl.col("valeur_fonciere").sum().alias("prix_total"),
        pl.col("surface_reelle_bati").sum().alias("surf_appart_tot"),
        pl.col("date_mutation").max().alias("date_any"),
        pl.col("nom_commune").max().alias("nom_commune"),
        pl.col("code_commune").max().alias("code_commune"),
        pl.col("code_departement").max().alias("code_departement"),
        pl.col("nombre_pieces_principales").max().alias("nombre_pieces_principales"),
    )
    return agg.filter(
        (pl.col("n_apparts") == 1)
        & (pl.col("nombre_pieces_principales").is_not_null())
        & (pl.col("nombre_pieces_principales") <= 5)
        & (pl.col("surf_appart_tot").is_not_null())
        & (pl.col("surf_appart_tot") > 0)
    )


def add_derived_columns(df):
    """Ajoute prix_m2, piece_bucket, time_weight, age_days, w."""
    df = df.with_columns(
        (pl.col("prix_total") / pl.col("surf_appart_tot")).alias("prix_m2"),
    ).filter(pl.col("prix_m2") > 0)

    df = df.with_columns(
        pl.col("nombre_pieces_principales").clip(1, 5).cast(pl.Utf8).alias("piece_bucket"),
        (AS_OF_DATE - pl.col("date_any")).dt.total_days().alias("age_days"),
    )

    df = df.with_columns(
        (0.5 ** (pl.col("age_days").cast(pl.Float64) / HALF_LIFE_DAYS)).alias("time_weight"),
    )

    df = df.with_columns(
        pl.when(pl.col("time_weight").is_not_null() & (pl.col("time_weight") > 0))
        .then(pl.col("time_weight"))
        .otherwise(1.0)
        .alias("w"),
    )

    return df


def weighted_quantile_by_group(df, val_col, weight_col, quantiles, group_cols):
    """Calcule des quantiles pondérés par groupe via sort + cumsum."""
    sorted_df = df.sort(group_cols + [val_col])

    sorted_df = sorted_df.with_columns(
        pl.col(weight_col).cum_sum().over(group_cols).alias("_cum_w"),
        pl.col(weight_col).sum().over(group_cols).alias("_tot_w"),
    )

    results = []
    for alias, q in quantiles.items():
        qq = (
            sorted_df.filter(pl.col("_cum_w") >= q * pl.col("_tot_w"))
            .group_by(group_cols)
            .agg(pl.col(val_col).first().alias(alias))
        )
        results.append(qq)

    out = results[0]
    for r in results[1:]:
        out = out.join(r, on=group_cols, how="left")
    return out


def apply_surface_trimming(df):
    """Filtre p10-p90 surface, ajuste prix par élasticité taille."""
    group_cols = ["code_departement", "piece_bucket"]

    sq = weighted_quantile_by_group(
        df.filter(pl.col("surf_appart_tot").is_not_null() & (pl.col("w") > 0) & (pl.col("prix_m2") > 0)),
        "surf_appart_tot", "w",
        {"sp10": Q_LOW, "sp50": 0.5, "sp90": Q_HIGH},
        group_cols,
    )
    df = df.join(sq, on=group_cols, how="left")

    df = df.filter(
        pl.col("sp10").is_null() | pl.col("sp90").is_null()
        | ((pl.col("surf_appart_tot") >= pl.col("sp10")) & (pl.col("surf_appart_tot") <= pl.col("sp90")))
    )

    df = df.with_columns(
        pl.when(pl.col("sp50").is_not_null() & (pl.col("sp50") > 0))
        .then((pl.col("prix_m2").log() - BETA_SURFACE * (pl.col("surf_appart_tot").log() - pl.col("sp50").log())).exp())
        .otherwise(pl.col("prix_m2"))
        .alias("prix_m2_adj"),
    ).filter(pl.col("prix_m2_adj") > 0)

    return df


def apply_price_trimming(df):
    """Filtre p10-p90 sur prix ajusté."""
    group_cols = ["code_departement", "piece_bucket"]

    pq = weighted_quantile_by_group(df, "prix_m2_adj", "w", {"plo": Q_LOW, "phi": Q_HIGH}, group_cols)
    df = df.join(pq, on=group_cols, how="left")

    df = df.filter(
        pl.col("plo").is_null() | pl.col("phi").is_null()
        | ((pl.col("prix_m2_adj") >= pl.col("plo")) & (pl.col("prix_m2_adj") <= pl.col("phi")))
    )

    return df


def run_transform(data_dir=DATA_DIR):
    """Pipeline complet: charge -> filtre -> agrège -> trim -> VenteDATA."""
    print("Chargement des fichiers...")
    raw = load_and_combine(data_dir)
    print(f"  Total brut: {raw.shape[0]:,} lignes")

    filtered = filter_base(raw)
    print(f"  Après filtre Vente/Appart: {filtered.shape[0]:,}")

    mutations = aggregate_mutations(filtered)
    print(f"  Mutations uniques (single, <=5p): {mutations.shape[0]:,}")

    enriched = add_derived_columns(mutations)
    trimmed_surf = apply_surface_trimming(enriched)
    trimmed_prix = apply_price_trimming(trimmed_surf)

    out = trimmed_prix.select(
        "code_departement", "code_commune", "nom_commune", "piece_bucket",
        pl.col("prix_m2_adj").alias("prix_m2"),
        "time_weight", "surf_appart_tot", "age_days",
    )
    print(f"  VenteDATA finale: {out.shape[0]:,} lignes")
    return out


if __name__ == "__main__":
    t0 = time.perf_counter()
    df = run_transform()
    elapsed = time.perf_counter() - t0
    print(f"\nTerminé en {elapsed:.2f}s")
    print(df.head(10))
