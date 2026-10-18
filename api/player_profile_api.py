from pathlib import Path
from typing import Any

import duckdb
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROFILE_DB = PROJECT_ROOT / "output" / "reep-player-profile-enrichment.duckdb"

app = FastAPI(
    title="Fyucha FC Player Profile API",
    version="0.1.0",
    description="Read-only API for sourced Fyucha FC player profiles.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://fyuchafc.com", "https://www.fyuchafc.com"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def fetch_one(query: str, params: list[Any] | None = None):
    if not PROFILE_DB.exists():
        raise HTTPException(
            status_code=503,
            detail="Player profile database is not available.",
        )

    con = duckdb.connect(str(PROFILE_DB), read_only=True)
    try:
        return con.execute(query, params or []).fetchone()
    finally:
        con.close()


def fetch_all(query: str, params: list[Any] | None = None):
    if not PROFILE_DB.exists():
        raise HTTPException(
            status_code=503,
            detail="Player profile database is not available.",
        )

    con = duckdb.connect(str(PROFILE_DB), read_only=True)
    try:
        return con.execute(query, params or []).fetchall()
    finally:
        con.close()


@app.get("/")
def root():
    return {
        "name": "Fyucha FC Player Profile API",
        "version": app.version,
        "read_only": True,
    }


@app.get("/api/health")
def health():
    try:
        row = fetch_one("""
            SELECT
                (SELECT COUNT(*) FROM player_profiles),
                (SELECT COUNT(*) FROM player_facts),
                (SELECT COUNT(*) FROM enrichment_sources),
                (SELECT metadata_value
                 FROM database_metadata
                 WHERE metadata_key = 'schema_version')
        """)
        return {
            "status": "ok",
            "database": PROFILE_DB.name,
            "profiles": row[0],
            "facts": row[1],
            "sources": row[2],
            "schema_version": row[3],
            "read_only": True,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Profile database health check failed: {type(exc).__name__}",
        ) from exc


@app.get("/api/search")
def search_players(q: str, limit: int = 20):
    """Search player names, ranking stronger matches first."""
    query = q.strip()
    if len(query) < 2:
        raise HTTPException(
            status_code=400,
            detail="Enter at least two characters to search.",
        )

    limit = max(1, min(limit, 50))
    pattern = "%" + query + "%"

    rows = fetch_all("""
        SELECT
            f.fact_value AS canonical_name,
            p.primary_position,
            p.profile_status
        FROM player_profiles p
        JOIN player_facts f
          ON f.reep_id = p.reep_id
         AND f.fact_type = 'canonical_name'
        WHERE lower(f.fact_value) LIKE lower(?)
        ORDER BY
            CASE
                WHEN lower(f.fact_value) = lower(?) THEN 0
                WHEN lower(f.fact_value) LIKE lower(?) THEN 1
                WHEN regexp_matches(
                    lower(f.fact_value),
                    '(^|[^a-z])' || lower(?) || '([^a-z]|$)'
                ) THEN 2
                ELSE 3
            END,
            length(f.fact_value),
            f.fact_value
        LIMIT ?
    """, [pattern, query, query + "%", query, limit])

    return {
        "query": query,
        "total": len(rows),
        "results": [
            {
                "name": name,
                "position": position,
                "profile_status": status,
            }
            for name, position, status in rows
        ],
    }

@app.get("/api/players/{reep_id}")
def get_player_profile(reep_id: str):
    row = fetch_one("""
        SELECT
            p.profile_status,
            p.biography,
            p.nationality,
            p.primary_position,
            p.preferred_foot,
            p.date_of_birth,
            p.birthplace,
            p.height_cm,
            p.weight_kg,
            f.fact_value AS canonical_name
        FROM player_profiles p
        LEFT JOIN player_facts f
          ON f.reep_id = p.reep_id
         AND f.fact_type = 'canonical_name'
        WHERE p.reep_id = ?
        LIMIT 1
    """, [reep_id])

    if row is None:
        raise HTTPException(status_code=404, detail="Player profile not found.")

    (
        profile_status,
        biography,
        nationality,
        primary_position,
        preferred_foot,
        date_of_birth,
        birthplace,
        height_cm,
        weight_kg,
        canonical_name,
    ) = row

    fact_rows = fetch_all("""
        SELECT
            f.fact_type,
            f.fact_value,
            f.verification_status,
            s.source_name,
            s.source_url
        FROM player_facts f
        LEFT JOIN enrichment_sources s
          ON s.source_id = f.source_id
        WHERE f.reep_id = ?
          AND f.fact_type <> 'canonical_name'
        ORDER BY f.fact_type, s.source_name
    """, [reep_id])

    facts = [
        {
            "type": fact_type,
            "value": fact_value,
            "verification_status": verification_status,
            "source_name": source_name,
            "source_url": source_url,
        }
        for (
            fact_type,
            fact_value,
            verification_status,
            source_name,
            source_url,
        ) in fact_rows
    ]

    return {
        "name": canonical_name,
        "profile_status": profile_status,
        "biography": biography,
        "nationality": nationality,
        "position": primary_position,
        "preferred_foot": preferred_foot,
        "date_of_birth": date_of_birth.isoformat() if date_of_birth else None,
        "birthplace": birthplace,
        "height_cm": height_cm,
        "weight_kg": weight_kg,
        "facts": facts,
    }



