"""Sauvegarde complète de l'instance : export en flux et import par remplacement total.

Format : JSON Lines compressé en gzip. Première ligne = en-tête
`{"format": "fluxus-export", "version": 1, "schema": …, "exported_at": …, "counts": {…}}`, puis une
ligne par enregistrement `{"t": "<table>", "r": {colonne: valeur}}`, tables dans l'ordre des clés
étrangères. Les identifiants sont conservés (aucun remappage des liens).

Hors du fichier : mot de passe, jetons d'appareils, clés API (chiffrées avec la clé secrète de
l'instance source), file de jobs, journal de consommation LLM, réglages internes (`_…`).
"""

from __future__ import annotations

import asyncio
import gzip
import json
import secrets
import zlib
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import islice
from typing import IO, Any

from fastapi import HTTPException, status
from sqlalchemy import DateTime, Table, func, insert, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from ..maintenance import MAINTENANCE_LOCK_KEY, Busy
from ..models import Category, Entry, Feed, NewsletterAddress, Setting, SmartFeed, SmartFeedMatch, Summary, Transcript
from ..settings_store import DEFAULTS, LAYOUT_VERSION_KEY, SECRET_KEYS, get_layout_version, set_setting

FORMAT = "fluxus-export"
VERSION = 1
SYNC_EPOCH_KEY = "_sync_epoch"
# Époque d'une instance jamais restaurée : aucun appareil n'a de cache à invalider.
INITIAL_EPOCH = "0"
BATCH = 1000
# Ligne décompressée la plus longue acceptée (un article et ses métadonnées) : borne la mémoire face
# à un fichier forgé.
MAX_LINE_CHARS = 64 * 1024 * 1024
LOCK_WAIT_SECONDS = 60

# Colonnes calculées par la base, jamais exportées.
SKIPPED_COLUMNS = {"search_vector"}


@dataclass(frozen=True)
class ExportTable:
    name: str
    model: type


# Ordre des clés étrangères. Toute nouvelle table de données utilisateur s'ajoute ici.
EXPORT_TABLES: tuple[ExportTable, ...] = (
    ExportTable("categories", Category),
    ExportTable("feeds", Feed),
    ExportTable("newsletter_addresses", NewsletterAddress),
    ExportTable("smart_feeds", SmartFeed),
    ExportTable("entries", Entry),
    ExportTable("summaries", Summary),
    ExportTable("transcripts", Transcript),
    ExportTable("smart_feed_matches", SmartFeedMatch),
    ExportTable("settings", Setting),
)
TABLES = {t.name: t for t in EXPORT_TABLES}
# Vidées à l'import, dans l'ordre des dépendances (les jobs se rapportent à l'ancien contenu). Le
# journal `llm_usage` est conservé : le budget quotidien se calcule dessus ; ses liens vers les
# articles et flux intelligents supprimés passent à NULL (ON DELETE SET NULL).
CLEARED = ("smart_feed_matches", "transcripts", "summaries", "jobs", "entries", "newsletter_addresses", "feeds", "smart_feeds", "categories")
# Tables à clé `id` auto-incrémentée : séquence recalée après l'import.
SEQUENCED = ("categories", "feeds", "newsletter_addresses", "smart_feeds", "entries", "summaries")


class InvalidArchive(ValueError):
    """Fichier illisible ou incompatible ; le message est destiné à l'utilisateur."""


def _table(model: type) -> Table:
    return model.__table__  # type: ignore[attr-defined]


def _columns(model: type) -> list:
    return [c for c in _table(model).columns if c.name not in SKIPPED_COLUMNS]


def _exported_setting(key: str) -> bool:
    """Réglages transportés : ceux de l'interface, hors secrets et clés internes."""
    return key in DEFAULTS and key not in SECRET_KEYS and not key.startswith("_")


def _where(t: ExportTable):
    return Setting.key.in_([k for k in DEFAULTS if _exported_setting(k)]) if t.model is Setting else None


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Valeur non sérialisable : {type(value).__name__}")


def _line(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, default=_json_default, separators=(",", ":")) + "\n").encode()


async def schema_revision(session: AsyncSession) -> str | None:
    try:
        async with session.begin_nested():
            return (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar_one_or_none()
    except Exception:  # noqa: BLE001 (base créée sans Alembic, en test)
        return None


async def export_stream(session: AsyncSession) -> AsyncIterator[bytes]:
    """Octets gzip de l'export, produits au fil de la lecture (instantané cohérent : REPEATABLE READ)."""
    await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    counts: dict[str, int] = {}
    for t in EXPORT_TABLES:
        q = select(func.count()).select_from(_table(t.model))
        where = _where(t)
        counts[t.name] = int((await session.execute(q.where(where) if where is not None else q)).scalar_one())
    header = {
        "format": FORMAT,
        "version": VERSION,
        "schema": await schema_revision(session),
        "exported_at": datetime.now(UTC).isoformat(),
        "counts": counts,
    }
    gz = zlib.compressobj(6, zlib.DEFLATED, 31)
    yield gz.compress(_line(header))
    for t in EXPORT_TABLES:
        cols = _columns(t.model)
        q = select(*cols)
        where = _where(t)
        if where is not None:
            q = q.where(where)
        pk = list(_table(t.model).primary_key.columns)
        result = await session.stream(q.order_by(*pk).execution_options(yield_per=500))
        buf: list[bytes] = []
        async for row in result.mappings():
            buf.append(_line({"t": t.name, "r": dict(row)}))
            if len(buf) >= 200:
                chunk = gz.compress(b"".join(buf))
                buf.clear()
                if chunk:
                    yield chunk
        if buf:
            yield gz.compress(b"".join(buf))
    yield gz.flush()
    await session.rollback()


# --- Import -------------------------------------------------------------------------------------


def _lines(fileobj: IO[bytes]) -> Iterator[tuple[int, dict]]:
    fileobj.seek(0)
    try:
        with gzip.open(fileobj, "rt", encoding="utf-8") as fh:
            n = 0
            while True:
                raw = fh.readline(MAX_LINE_CHARS)
                if not raw:
                    return
                n += 1
                if len(raw) >= MAX_LINE_CHARS and not raw.endswith("\n"):
                    raise InvalidArchive(f"Ligne {n} trop longue : fichier corrompu ou forgé")
                if raw.strip():
                    obj = json.loads(raw)
                    if not isinstance(obj, dict):
                        raise InvalidArchive(f"Ligne {n} : objet JSON attendu")
                    yield n, obj
    except (OSError, EOFError, zlib.error, UnicodeDecodeError) as exc:
        raise InvalidArchive("Fichier illisible : ce n'est pas une sauvegarde Fluxus compressée (.jsonl.gz) ou elle est tronquée") from exc
    except json.JSONDecodeError as exc:
        raise InvalidArchive(f"Fichier corrompu : JSON invalide ({exc.msg})") from exc


def _check_header(obj: dict) -> dict:
    if obj.get("format") != FORMAT:
        raise InvalidArchive("Ce fichier n'est pas une sauvegarde Fluxus")
    if obj.get("version") != VERSION:
        raise InvalidArchive(f"Version de sauvegarde {obj.get('version')} non prise en charge par cette instance (version {VERSION}) : mettez Fluxus à jour")
    return obj


def read_header(fileobj: IO[bytes]) -> dict:
    for _, obj in _lines(fileobj):
        return _check_header(obj)
    raise InvalidArchive("Fichier vide")


def validate_archive(fileobj: IO[bytes]) -> dict:
    """Lecture complète sans rien écrire : en-tête, tables et colonnes connues, comptes cohérents."""
    header: dict | None = None
    known = {t.name: {c.name for c in _columns(t.model)} for t in EXPORT_TABLES}
    seen: dict[str, int] = {}
    order = [t.name for t in EXPORT_TABLES]
    last = -1
    for n, obj in _lines(fileobj):
        if header is None:
            header = _check_header(obj)
            continue
        table, row = obj.get("t"), obj.get("r")
        if table not in known or not isinstance(row, dict):
            raise InvalidArchive(f"Ligne {n} : table inconnue « {table} » (sauvegarde d'une version plus récente ?)")
        unknown = set(row) - known[table]
        if unknown:
            raise InvalidArchive(f"Ligne {n} : colonnes inconnues dans « {table} » : {', '.join(sorted(unknown))} (sauvegarde d'une version plus récente ?)")
        idx = order.index(table)
        if idx < last:
            raise InvalidArchive(f"Ligne {n} : tables dans le désordre (« {table} » après « {order[last]} »)")
        last = idx
        seen[table] = seen.get(table, 0) + 1
    if header is None:
        raise InvalidArchive("Fichier vide")
    expected = header.get("counts") or {}
    for table, count in expected.items():
        if table in known and seen.get(table, 0) != count:
            raise InvalidArchive(f"Sauvegarde incomplète : {seen.get(table, 0)} lignes « {table} » au lieu de {count}")
    return header


def _convert(model: type, row: dict) -> dict:
    out = dict(row)
    for col in _columns(model):
        value = out.get(col.name)
        if isinstance(value, str) and isinstance(col.type, DateTime):
            out[col.name] = datetime.fromisoformat(value)
    return out


async def _take_maintenance_lock(session: AsyncSession) -> None:
    """Verrou exclusif (transaction) : attend la fin des jobs en cours, bloque les suivants jusqu'au commit.

    Attente bloquante bornée par `lock_timeout` plutôt que des essais répétés : la demande prend son
    rang dans la file de Postgres, les nouveaux verrous partagés attendent derrière elle au lieu de
    l'affamer."""
    await session.execute(text(f"SET LOCAL lock_timeout = '{LOCK_WAIT_SECONDS}s'"))
    try:
        await session.execute(select(func.pg_advisory_xact_lock(MAINTENANCE_LOCK_KEY)))
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "55P03":  # lock_not_available
            raise Busy("Des tâches de fond (récupération, résumés) sont en cours : réessayez dans une minute") from exc
        raise
    await session.execute(text("SET LOCAL lock_timeout = DEFAULT"))


async def import_replace(session: AsyncSession, fileobj: IO[bytes]) -> tuple[dict, dict[str, int]]:
    """Remplace tout le contenu de l'instance par la sauvegarde (une transaction : tout ou rien).

    Conserve le mot de passe, les jetons d'appareils, les clés API, les réglages internes et le
    journal LLM de la cible. Change l'époque de synchronisation : chaque appareil vide son cache et
    repart de zéro. La lecture du fichier (gzip, JSON) se fait hors de la boucle asyncio.
    Renvoie (en-tête, lignes insérées par table). L'appelant commit.
    """
    header = await asyncio.to_thread(validate_archive, fileobj)
    await _take_maintenance_lock(session)
    for name in CLEARED:
        await session.execute(text(f"DELETE FROM {name}"))
    await session.execute(Setting.__table__.delete().where(Setting.key.in_([k for k in DEFAULTS if _exported_setting(k)])))

    inserted: dict[str, int] = {}
    lines = _lines(fileobj)

    def next_chunk() -> list[tuple[int, dict]]:
        return list(islice(lines, BATCH))

    batch: list[dict] = []
    current: ExportTable | None = None

    async def flush() -> None:
        if current is not None and batch:
            await session.execute(insert(current.model), batch)
            inserted[current.name] = inserted.get(current.name, 0) + len(batch)
            batch.clear()

    while chunk := await asyncio.to_thread(next_chunk):
        for _, obj in chunk:
            table = obj.get("t")
            if table is None:
                continue  # en-tête
            t = TABLES[table]
            if t.model is Setting and not _exported_setting(obj["r"].get("key", "")):
                continue
            if current is not t:
                await flush()
                current = t
            batch.append(_convert(t.model, obj["r"]))
            if len(batch) >= BATCH:
                await flush()
    await flush()

    for name in SEQUENCED:
        await session.execute(
            text(f"SELECT setval(pg_get_serial_sequence('{name}', 'id'), coalesce((SELECT max(id) FROM {name}), 0) + 1, false)")
        )
    # Tableau de bord importé : les appareils doivent l'adopter plutôt que pousser leur copie.
    await set_setting(session, LAYOUT_VERSION_KEY, str(await get_layout_version(session) + 1))
    await set_setting(session, SYNC_EPOCH_KEY, secrets.token_urlsafe(12))
    return header, inserted


async def sync_epoch(session: AsyncSession) -> str:
    """Époque de synchronisation : `INITIAL_EPOCH` tant qu'aucune sauvegarde n'a été importée, puis une
    valeur aléatoire à chaque import (lecture seule : pas d'écriture concurrente au premier appel)."""
    row = await session.get(Setting, SYNC_EPOCH_KEY)
    return row.value if row is not None else INITIAL_EPOCH


async def check_epoch(session: AsyncSession, epoch: str | None, detail: str) -> None:
    """Garde des écritures client rejouables (file hors ligne) : 409 si la base a été remplacée depuis
    l'époque connue du client.

    Verrou de maintenance pris en partagé jusqu'à la fin de la transaction : un import ne peut pas
    s'intercaler entre ce contrôle et les écritures qui suivent. Un client sans époque (version
    antérieure à la sauvegarde complète) n'est accepté que sur une instance jamais restaurée."""
    await session.execute(select(func.pg_advisory_xact_lock_shared(MAINTENANCE_LOCK_KEY)))
    if (epoch or INITIAL_EPOCH) != await sync_epoch(session):
        raise HTTPException(status.HTTP_409_CONFLICT, detail)
