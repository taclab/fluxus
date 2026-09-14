"""Schémas Pydantic partagés par les routers."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ExtractionMode = Literal["none", "http", "browser", "auto"]


class CategoryOut(BaseModel):
    id: int
    title: str
    position: int
    unread_count: int = 0


class CategoryIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    position: int | None = None


class FeedOut(BaseModel):
    id: int
    category_id: int | None
    url: str
    site_url: str | None
    title: str
    description: str | None
    icon_url: str | None
    last_fetched_at: datetime | None
    next_fetch_at: datetime
    fetch_interval_min: int
    error_count: int
    last_error: str | None
    extraction_mode: str
    is_youtube: bool
    disabled: bool
    unread_count: int = 0
    updated_at: datetime


class FeedCreate(BaseModel):
    url: str = Field(min_length=4, max_length=2048)
    category_id: int | None = None
    title: str | None = Field(default=None, max_length=500)
    extraction_mode: ExtractionMode = "auto"


class FeedUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    url: str | None = Field(default=None, max_length=2048)
    category_id: int | None = None
    clear_category: bool = False
    extraction_mode: ExtractionMode | None = None
    disabled: bool | None = None
    fetch_interval_min: int | None = Field(default=None, ge=5, le=7 * 24 * 60)


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
