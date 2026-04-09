"""
Stats commune x bucket, shrink départemental, jointure loyers, sortie finale.
"""

import polars as pl
from pathlib import Path
from transform import run_transform, weighted_quantile_by_group
import unicodedata
import re
import time

# ============================
# Paramètres
# ============================
MIN_N = 20
USE_LOYER = True
USE_SHRINK = False
LAMBDA = 75

DATA_DIR = Path(__file__).parent.parent / "data"
OUTPUT_DIR = Path(__file__).parent.parent / "output"
LOYER_1OU2P = DATA_DIR / "loyer_1ou2p.csv"
LOYER_3P = DATA_DIR / "loyer_3p.csv"


def normalize_text(s):
    """Supprime accents, upper, nettoie espaces/tirets/apostrophes."""
    if s is None:
        return None
    nfkd = unicodedata.normalize("NFKD", s.upper().strip())
    no_acc = "".join(c for c in nfkd if not unicodedata.combining(c))
    no_acc = no_acc.replace("AE", "AE").replace("OE", "OE")
    parts = re.split(r"[\s\-']+", no_acc)
    return " ".join(p for p in parts if p)


def weighted_stats_by_group(df):
    """Calcule toutes les stats pondérées par (dept, commune, bucket)."""
    group_cols = ["code_departement", "code_commune", "nom_commune", "piece_bucket"]
    valid = df.filter(pl.col("prix_m2").is_not_null() & (pl.col("w") > 0))

    # Quantiles prix
    pq = weighted_quantile_by_group(
        valid, "prix_m2", "w",
        {"ref_raw": 0.5, "p25": 0.25, "p75": 0.75},
        group_cols,
    )

    # Quantiles surface et age
    sq = weighted_quantile_by_group(
        valid.filter(pl.col("surf_appart_tot").is_not_null()),
        "surf_appart_tot", "w", {"surf_med": 0.5}, group_cols,
    )
    aq = weighted_quantile_by_group(
        valid.filter(pl.col("age_days").is_not_null()),
        "age_days", "w", {"age_med_j": 0.5}, group_cols,
    )

    # Aggregations simples
    simple = valid.group_by(group_cols).agg(
        pl.len().alias("n_mut"),
        (pl.col("prix_m2") * pl.col("w")).sum().alias("_num"),
        pl.col("w").sum().alias("_w_tot"),
        (pl.col("w") ** 2).sum().alias("_w2_sum"),
    ).with_columns(
        (pl.col("_num") / pl.col("_w_tot")).alias("mean_w"),
        (pl.col("_w_tot") ** 2 / pl.col("_w2_sum")).alias("n_eff"),
    ).drop("_num", "_w_tot", "_w2_sum")

    # MAD pondéré
    with_med = valid.join(pq.select(group_cols + ["ref_raw"]), on=group_cols, how="left")
    with_dev = with_med.with_columns(
        (pl.col("prix_m2") - pl.col("ref_raw")).abs().alias("abs_dev"),
    )
    mad_q = weighted_quantile_by_group(
        with_dev, "abs_dev", "w", {"_mad_raw": 0.5}, group_cols,
    ).with_columns(
        (pl.col("_mad_raw") * 1.4826).alias("mad_w"),
    ).drop("_mad_raw")

    out = simple
    for t in [pq, sq, aq, mad_q]:
        out = out.join(t, on=group_cols, how="left")

    return out.filter(pl.col("ref_raw").is_not_null() & (pl.col("n_mut") >= MIN_N))


def apply_shrink(df, vente_df):
    """Shrinkage optionnel vers médiane départementale."""
    if not USE_SHRINK:
        return df.with_columns(
            pl.col("ref_raw").alias("ref_m2"),
            pl.lit("commune").alias("source_level"),
        )

    dept_q = weighted_quantile_by_group(
        vente_df.filter(pl.col("prix_m2").is_not_null() & (pl.col("w") > 0)),
        "prix_m2", "w", {"ref_dept": 0.5},
        ["code_departement", "piece_bucket"],
    )
    df = df.join(dept_q, on=["code_departement", "piece_bucket"], how="left")
    alpha = pl.col("n_eff") / (pl.col("n_eff") + LAMBDA)

    df = df.with_columns(
        pl.when(pl.col("ref_dept").is_not_null())
        .then(alpha * pl.col("ref_raw") + (1 - alpha) * pl.col("ref_dept"))
        .otherwise(pl.col("ref_raw"))
        .alias("ref_m2"),
        pl.when(pl.col("ref_dept").is_not_null())
        .then(pl.lit("commune_shrink"))
        .otherwise(pl.lit("commune"))
        .alias("source_level"),
    ).drop("ref_dept")

    return df


def load_loyers():
    """Charge les deux fichiers loyers et produit une table LIBGEO -> (L1_2, L3p)."""
    dfs = {}
    for path, alias in [(LOYER_1OU2P, "L1_2"), (LOYER_3P, "L3p")]:
        if not path.exists():
            print(f"  ⚠ Fichier loyer introuvable: {path}")
            continue
        df = pl.read_csv(path, ignore_errors=True)
        df = df.select(
            pl.col("LIBGEO").cast(pl.Utf8),
            pl.col("loypredm2").cast(pl.Float64, strict=False).alias(alias),
        ).filter(pl.col("LIBGEO").is_not_null())

        # Normaliser + dédoublonner par commune
        df = df.with_columns(
            pl.col("LIBGEO").map_elements(normalize_text, return_dtype=pl.Utf8).alias("k"),
        ).group_by("k").agg(pl.col(alias).median())
        dfs[alias] = df

    if len(dfs) < 2:
        return None

    loyers = dfs["L1_2"].join(dfs["L3p"], on="k", how="outer_coalesce")
    return loyers


def join_loyers(df):
    """Jointure loyers par nom de commune normalisé."""
    if not USE_LOYER:
        return df.with_columns(pl.lit(None).cast(pl.Float64).alias("Loyer"))

    loyers = load_loyers()
    if loyers is None:
        return df.with_columns(pl.lit(None).cast(pl.Float64).alias("Loyer"))

    print(f"  Loyers chargés: {loyers.shape[0]} communes")

    df = df.with_columns(
        pl.col("nom_commune").map_elements(normalize_text, return_dtype=pl.Utf8).alias("k"),
    )

    df = df.join(loyers, on="k", how="left").with_columns(
        pl.when(pl.col("piece_bucket").is_in(["1", "2"]))
        .then(pl.col("L1_2"))
        .otherwise(pl.col("L3p"))
        .alias("Loyer"),
    ).drop("k", "L1_2", "L3p")

    matched = df.filter(pl.col("Loyer").is_not_null()).shape[0]
    total = df.shape[0]
    print(f"  Loyers matchés: {matched}/{total} ({100*matched//total}%)")

    return df


def run_analytics():
    """Pipeline complet analytics."""
    t0 = time.perf_counter()

    # 1) VenteDATA
    vente = run_transform()
    vente = vente.with_columns(
        pl.when(pl.col("time_weight").is_not_null() & (pl.col("time_weight") > 0))
        .then(pl.col("time_weight"))
        .otherwise(1.0)
        .alias("w"),
    )

    # 2) Stats commune × bucket
    print("\nCalcul des stats par commune...")
    stats = weighted_stats_by_group(vente)
    print(f"  {stats.shape[0]} lignes (communes × buckets avec n >= {MIN_N})")

    # 3) Shrink
    stats = apply_shrink(stats, vente)

    # 4) Loyers
    print("\nJointure loyers...")
    stats = join_loyers(stats)

    # 5) Tri
    stats = stats.with_columns(
        pl.col("nom_commune").map_elements(normalize_text, return_dtype=pl.Utf8).alias("nom_sort"),
        pl.col("piece_bucket").cast(pl.Int32, strict=False).alias("piece_order"),
    ).sort("nom_sort", "piece_order").drop("nom_sort", "piece_order")

    # 6) Colonnes de sortie
    out = stats.select(
        "code_departement", "code_commune", "nom_commune", "piece_bucket",
        "ref_m2", "ref_raw", "mean_w", "n_mut", "n_eff", "mad_w", "p25", "p75",
        "surf_med", "age_med_j", "Loyer", "source_level",
    )

    elapsed = time.perf_counter() - t0
    print(f"\nTerminé: {out.shape[0]} lignes en {elapsed:.2f}s")
    return out


def export(df, output_dir=OUTPUT_DIR):
    """Exporte en CSV + Excel."""
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "resultats.csv"
    xlsx_path = output_dir / "resultats.xlsx"

    df.write_csv(csv_path)
    df.to_pandas().to_excel(xlsx_path, index=False, sheet_name="Références")

    print(f"\nExport:")
    print(f"  CSV:   {csv_path}")
    print(f"  Excel: {xlsx_path}")


if __name__ == "__main__":
    result = run_analytics()
    print(result.head(15))
    export(result)
