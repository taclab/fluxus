from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

# JSON portable : JSONB sur PostgreSQL, JSON ailleurs (tests SQLite).
JsonType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    from datetime import UTC

    return datetime.now(UTC)


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), unique=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    feeds: Mapped[list[Feed]] = relationship(back_populates="category")


class Feed(Base):
    __tablename__ = "feeds"

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    url: Mapped[str] = mapped_column(String(2048), unique=True)
    site_url: Mapped[str | None] = mapped_column(String(2048))
    title: Mapped[str] = mapped_column(String(500), default="")
    description: Mapped[str | None] = mapped_column(Text)
    icon_url: Mapped[str | None] = mapped_column(String(2048))
    # Ordre manuel dans son dossier (un nouveau flux prend min(position) - 1 : il arrive en tête)
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    etag: Mapped[str | None] = mapped_column(String(500))
    last_modified: Mapped[str | None] = mapped_column(String(200))
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_fetch_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    fetch_interval_min: Mapped[int] = mapped_column(Integer, default=30)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)

    # none | http | browser | auto
    extraction_mode: Mapped[str] = mapped_column(String(20), default="auto")
    is_youtube: Mapped[bool] = mapped_column(Boolean, default=False)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    category: Mapped[Category | None] = relationship(back_populates="feeds")
    entries: Mapped[list[Entry]] = relationship(
        back_populates="feed", cascade="all, delete-orphan", passive_deletes=True
    )


class Entry(Base):
    __tablename__ = "entries"
    __table_args__ = (
        UniqueConstraint("feed_id", "guid", name="uq_entries_feed_guid"),
        Index("ix_entries_published_at", "published_at"),
        Index("ix_entries_updated_at", "updated_at"),
        Index("ix_entries_feed_read", "feed_id", "read"),
        Index("ix_entries_hash", "hash"),
        Index("ix_entries_search_vector", "search_vector", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    feed_id: Mapped[int] = mapped_column(
        ForeignKey("feeds.id", ondelete="CASCADE"), index=True
    )
    guid: Mapped[str] = mapped_column(String(2048))
    url: Mapped[str | None] = mapped_column(String(2048))
    title: Mapped[str] = mapped_column(String(1000), default="")
    author: Mapped[str | None] = mapped_column(String(300))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # Contenu tel que fourni par le flux, nettoyé.
    summary_html: Mapped[str | None] = mapped_column(Text)
    # Article complet extrait du site (phase 2).
    content_html: Mapped[str | None] = mapped_column(Text)
    content_text: Mapped[str | None] = mapped_column(Text)
    # none | pending | done | failed
    extraction_status: Mapped[str] = mapped_column(String(20), default="none")
    extraction_error: Mapped[str | None] = mapped_column(Text)

    read: Mapped[bool] = mapped_column(Boolean, default=False)
    starred: Mapped[bool] = mapped_column(Boolean, default=False)
    # Empreinte de déduplication (URL normalisée ou titre+date).
    hash: Mapped[str] = mapped_column(String(64))
    # Métadonnées libres : enclosures, image de couverture, id vidéo YouTube...
    meta: Mapped[dict] = mapped_column(JsonType, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    # Colonne de recherche plein texte, alimentée par un trigger (PostgreSQL uniquement).
    search_vector = mapped_column(TSVECTOR().with_variant(Text(), "sqlite"), nullable=True)

    feed: Mapped[Feed] = relationship(back_populates="entries")
    summaries: Mapped[list[Summary]] = relationship(
        back_populates="entry", cascade="all, delete-orphan", passive_deletes=True
    )
    transcript: Mapped[Transcript | None] = relationship(
        back_populates="entry", cascade="all, delete-orphan", uselist=False, passive_deletes=True
    )


class Summary(Base):
    __tablename__ = "summaries"

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(
        ForeignKey("entries.id", ondelete="CASCADE"), index=True
    )
    # article | video
    kind: Mapped[str] = mapped_column(String(20), default="article")
    provider: Mapped[str] = mapped_column(String(50), default="anthropic")
    model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(20), default="v1")
    text_md: Mapped[str] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    # manual | auto (résumé déclenché par un flux intelligent)
    origin: Mapped[str] = mapped_column(String(10), default="manual", server_default="manual")
    smart_feed_id: Mapped[int | None] = mapped_column(
        ForeignKey("smart_feeds.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    entry: Mapped[Entry] = relationship(back_populates="summaries")


class SmartFeed(Base):
    """Recherche enregistrée : une vue par sujet à travers tout, une catégorie ou un flux."""

    __tablename__ = "smart_feeds"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    # all | category | feed
    scope_kind: Mapped[str] = mapped_column(String(20), default="all")
    scope_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # keywords | regex
    mode: Mapped[str] = mapped_column(String(20), default="keywords")
    keywords_any: Mapped[list] = mapped_column(JsonType, default=list)
    keywords_all: Mapped[list] = mapped_column(JsonType, default=list)
    keywords_none: Mapped[list] = mapped_column(JsonType, default=list)
    regex: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # {"title": bool, "excerpt": bool, "author": bool, "url": bool, "content": bool}
    fields: Mapped[dict] = mapped_column(JsonType, default=dict)
    auto_summary: Mapped[bool] = mapped_column(Boolean, default=False)
    summary_daily_cap: Mapped[int] = mapped_column(Integer, default=10)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class SmartFeedMatch(Base):
    __tablename__ = "smart_feed_matches"
    __table_args__ = (Index("ix_smart_feed_matches_entry", "entry_id"),)

    smart_feed_id: Mapped[int] = mapped_column(
        ForeignKey("smart_feeds.id", ondelete="CASCADE"), primary_key=True
    )
    entry_id: Mapped[int] = mapped_column(ForeignKey("entries.id", ondelete="CASCADE"), primary_key=True)
    matched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Transcript(Base):
    __tablename__ = "transcripts"

    entry_id: Mapped[int] = mapped_column(
        ForeignKey("entries.id", ondelete="CASCADE"), primary_key=True
    )
    language: Mapped[str] = mapped_column(String(10), default="")
    text: Mapped[str] = mapped_column(Text)
    segments: Mapped[list] = mapped_column(JsonType, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    entry: Mapped[Entry] = relationship(back_populates="transcript")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status_run_at", "status", "run_at"),
        Index("ix_jobs_dedupe_key", "dedupe_key", unique=True, postgresql_where=text("dedupe_key IS NOT NULL")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict] = mapped_column(JsonType, default=dict)
    # queued | running | done | failed
    status: Mapped[str] = mapped_column(String(20), default="queued")
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    last_error: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Clé d'unicité pour éviter d'empiler deux fois le même job en attente.
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class DeviceToken(Base):
    __tablename__ = "device_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# Extensions PostgreSQL requises (migration et fixture de tests). `unaccent` est une extension « trusted ».
EXTENSION_STATEMENTS = ["CREATE EXTENSION IF NOT EXISTS unaccent"]

# Trigger PostgreSQL pour la recherche plein texte (créé par la migration initiale).
# Une instruction par élément : asyncpg n'accepte pas plusieurs instructions par requête.
SEARCH_VECTOR_STATEMENTS = [
    """
CREATE OR REPLACE FUNCTION entries_search_vector_update() RETURNS trigger AS $$
BEGIN
  NEW.search_vector :=
    setweight(to_tsvector('simple', coalesce(NEW.title, '')), 'A') ||
    setweight(to_tsvector('simple', coalesce(NEW.author, '')), 'C') ||
    setweight(to_tsvector('simple', left(coalesce(NEW.content_text, regexp_replace(coalesce(NEW.summary_html, ''), '<[^>]+>', ' ', 'g')), 50000)), 'B');
  RETURN NEW;
END
$$ LANGUAGE plpgsql
""",
    "DROP TRIGGER IF EXISTS trg_entries_search_vector ON entries",
    """
CREATE TRIGGER trg_entries_search_vector
  BEFORE INSERT OR UPDATE OF title, author, summary_html, content_text ON entries
  FOR EACH ROW EXECUTE FUNCTION entries_search_vector_update()
""",
]
