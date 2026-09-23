"""Schémas Pydantic partagés par les routers."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

ExtractionMode = Literal["none", "http", "browser", "auto"]
# Présentation de la liste d'une vue : compact et cards gardent l'article à côté de la liste,
# les autres occupent toute la largeur et ouvrent l'article dans une fenêtre.
ViewMode = Literal["compact", "cards", "grid", "magazine", "list", "titles"]
VIEW_MODES: tuple[str, ...] = ("compact", "cards", "grid", "magazine", "list", "titles")
# En écriture, « inherit » efface la présentation propre (retour au dossier ou au défaut).
ViewModeIn = Literal["compact", "cards", "grid", "magazine", "list", "titles", "inherit"]


def view_mode_value(value: str) -> str | None:
    """Valeur à stocker pour une présentation reçue : null pour « inherit »."""
    return None if value == "inherit" else value


class CategoryOut(BaseModel):
    id: int
    title: str
    position: int
    view_mode: ViewMode | None = None
    unread_count: int = 0


class CategoryIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    position: int | None = None
    view_mode: ViewModeIn | None = None


class FeedOut(BaseModel):
    id: int
    category_id: int | None
    position: int
    url: str
    site_url: str | None
    title: str
    description: str | None
    icon_url: str | None
    last_fetched_at: datetime | None
    next_fetch_at: datetime
    fetch_interval_min: int
    fetch_interval_fixed_min: int | None = None
    error_count: int
    last_error: str | None
    extraction_mode: str
    is_youtube: bool
    disabled: bool
    view_mode: ViewMode | None = None
    auto_summary: bool = False
    summary_daily_cap: int = 10
    unread_count: int = 0
    updated_at: datetime


class FeedCreate(BaseModel):
    url: str = Field(min_length=4, max_length=2048)
    category_id: int | None = None
    title: str | None = Field(default=None, max_length=500)
    extraction_mode: ExtractionMode = "auto"


class FeedSettingsIn(BaseModel):
    """Réglages applicables à un flux comme à une sélection de flux (écran Abonnements)."""

    category_id: int | None = None
    clear_category: bool = False
    extraction_mode: ExtractionMode | None = None
    disabled: bool | None = None
    view_mode: ViewModeIn | None = None
    auto_summary: bool | None = None
    summary_daily_cap: int | None = Field(default=None, ge=1, le=200)
    # Minutes entre deux récupérations ; 0 = intervalle adaptatif.
    fetch_interval_fixed_min: int | None = Field(default=None, ge=0, le=7 * 24 * 60)

    @field_validator("fetch_interval_fixed_min")
    @classmethod
    def _interval(cls, value: int | None) -> int | None:
        if value is not None and 0 < value < 5:
            raise ValueError("L'intervalle fixe est d'au moins 5 minutes")
        return value


class FeedUpdate(FeedSettingsIn):
    title: str | None = Field(default=None, max_length=500)
    url: str | None = Field(default=None, max_length=2048)


class FeedBulkIn(BaseModel):
    feed_ids: list[int] = Field(min_length=1, max_length=2000)
    changes: FeedSettingsIn


class FeedBulkOut(BaseModel):
    feeds: list[FeedOut]


class FeedStats(BaseModel):
    feed_id: int
    entries_7d: int
    entries_30d: int
    last_entry_at: datetime | None
    auto_summaries_today: int


class SummarizeAllIn(BaseModel):
    unread_only: bool = True


class SummarizeAllOut(BaseModel):
    queued: int
    skipped_budget: int
    already_summarized: int
    budget_remaining: int


class ReorderCategory(BaseModel):
    id: int
    position: int


class ReorderFeed(BaseModel):
    id: int
    position: int
    category_id: int | None = None


class ReorderSmartFeed(BaseModel):
    id: int
    position: int


class ReorderIn(BaseModel):
    """Nouvel ordre de la barre latérale : seules les listes touchées par un dépôt sont envoyées."""

    categories: list[ReorderCategory] = Field(default_factory=list, max_length=1000)
    feeds: list[ReorderFeed] = Field(default_factory=list, max_length=1000)
    smart_feeds: list[ReorderSmartFeed] = Field(default_factory=list, max_length=1000)


class DiscoverIn(BaseModel):
    url: str


class DiscoverOut(BaseModel):
    url: str
    title: str


class EntryListItem(BaseModel):
    id: int
    feed_id: int
    title: str
    url: str | None
    author: str | None
    published_at: datetime
    read: bool
    starred: bool
    excerpt: str
    cover: str | None
    youtube_id: str | None
    has_content: bool
    has_summary: bool
    smart_feed_ids: list[int] = Field(default_factory=list)
    updated_at: datetime


class EntryOut(EntryListItem):
    summary_html: str | None
    content_html: str | None
    extraction_status: str
    extraction_error: str | None
    meta: dict


class EntryPage(BaseModel):
    items: list[EntryListItem]
    next_cursor: str | None


class EntryPatch(BaseModel):
    read: bool | None = None
    starred: bool | None = None


class MarkReadIn(BaseModel):
    entry_ids: list[int] | None = None
    feed_id: int | None = None
    category_id: int | None = None
    smart_feed_id: int | None = None
    all: bool = False
    starred_only: bool = False
    before: datetime | None = None
    read: bool = True


class FetchContentIn(BaseModel):
    mode: Literal["http", "browser", "auto"] = "auto"


class SummaryOut(BaseModel):
    id: int
    entry_id: int
    kind: str
    provider: str
    model: str
    text_md: str
    input_tokens: int
    output_tokens: int
    created_at: datetime


class OpmlImportResult(BaseModel):
    imported: int
    skipped: int
    categories_created: int
