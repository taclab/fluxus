# User guide

The interface is available in French and English: it follows the browser language on first launch,
and *Settings → Appearance → Interface language* changes it (per device, like the theme). Labels
below are quoted in French, as they appear with the French interface.

## Interface

Three panes: **subscriptions** (collapsible folders, unread counts, red dot on a failing feed),
**article list** (filter « non lus », search, mark all read) and **reader** (feed content or full
article, embedded YouTube video, summary panel).

On tablets the sidebar becomes a drawer; on phones one pane is shown at a time. The URL encodes the
current view (`/feed/12?entry=345&unread=1`), so the back button and browser bookmarks work.

## Dashboard

Home page (`/`, key `h`). Out of the box it shows an overview (unread, read today, starred, unread
per folder, failing feeds), a news carousel, a magazine-style « Nouveautés » widget per folder,
the latest YouTube videos, starred articles and recent summaries. Articles without an image get a
colored placeholder with the feed icon. This automatic layout follows your folders until you first
customize it.

**Personnaliser** (top right) enters edit mode:

- **drag** a widget by its ⋮⋮ handle (mouse, long press, or keyboard: Space, arrows, Space);
- the ⋯ menu of each widget: move up / down, **width** ⅓ ½ 1, configure, remove;
- **Ajouter un widget**: type, source (a folder, a feed or everything), item count, list or cards,
  unread only, custom title.

| Widget | Content |
|---|---|
| Nouveautés | latest articles of a folder, a feed or everything; « tout marquer lu »; four displays: carousel, magazine, cards, list; star and read toggles on each article |
| Vidéos YouTube | thumbnails of the latest unwatched videos, all channels or one folder |
| Favoris | latest starred articles |
| Résumés récents | latest summarized articles, with the first lines of the summary |
| Statistiques | unread, read today, starred, summaries and tokens, bars per folder, failing feeds |

Clicking an article opens the reader in the context of its folder (`j`/`k` available); back returns
to the dashboard. On mobile every widget is full width and reorders through the ⋯ menu.

The layout is stored on the server, so it is the same on the web and on Android, and cached locally
for offline use. *Paramètres → Apparence → Réinitialiser le tableau de bord* restores the automatic
layout.

## Smart feeds

A smart feed is a **topic view**: "everything about X in my feeds". It appears in the sidebar with
its unread count, can be a dashboard widget source, and works offline.

Create one with « + nouveau » in the sidebar. The editor offers:

- a **scope**: all feeds, a folder or a feed;
- a **keyword** filter (any of, all of, none of), case- and accent-insensitive, or a **regular
  expression** in expert mode (PostgreSQL engine, unaccented text, no backreferences,
  quantifiers ≤ 255);
- the **fields** examined: title, excerpt, author, URL; optionally the full content, which only
  applies to articles already extracted;
- a **live preview** of the match count and eight examples;
- **automatic summaries** of new articles, with a daily quota for this smart feed.

Nothing is hidden or deleted: a smart feed only groups. Matches are computed when each article
arrives, recomputed when you edit the definition, and synced to all your devices.

**Automatic summaries** use a dedicated, cheaper model set in *Paramètres → Résumés*, and stop at
the **daily token budget** (200,000 by default, 0 to disable, UTC day). In a smart feed view,
✨ « Résumer les non résumés » queues the missing summaries within the remaining budget.

## Layouts

The **Présentation** button in each view's header offers six layouts in two families: **with the
article alongside** (Compacte, Cartes) and **full width** (Grille, Magazine, Liste, Titres: a click
opens the article in a centered window; ← → or the side buttons move to the neighbor, Esc closes;
the `m`, `s`, `v`, `f`, `r` shortcuts stay active).

The choice is **stored for the current view** and synced between devices. A feed without its own
layout follows **its folder**, then the **default layout** (*Paramètres → Affichage par défaut*),
which also applies to Tous, Non lus, Favoris and Recherche.

## Subscriptions screen

**Abonnements** (icon at the bottom of the sidebar) lists all feeds by folder, with their state
(active, paused, failing ×n), last and next fetch and 7-day activity. Each setting is edited in place:

- **pause**: the feed is no longer fetched;
- **interval**: automatic (tighter when the feed publishes a lot, looser when quiet) or fixed, from
  15 min to once a day;
- **full article**: on demand, or always (each new article is fetched in the background and
  readable offline), over HTTP or through the browser for JavaScript sites;
- **automatic summaries**: every new article of the feed is summarized, at most N per day, within
  the daily budget shared with smart feeds;
- **layout** of the feed and of each folder.

Search, filters and sorts; selecting several feeds shows a bulk action bar (pause, resume, move,
layout, full article, interval, automatic summaries, refresh, unsubscribe).

## Organizing the sidebar

A **long press** (mouse or finger, about a third of a second) picks up a folder, a feed or a smart
feed; then drop it:

- a folder above or below another root item (folder or feed without folder);
- a feed between two feeds, in its folder or another one;
- a feed on a folder header to put it first in that folder (a collapsed folder opens after a short hover);
- a feed on the **top edge** of a folder header to take it out of any folder, just before it; feeds
  without a folder then move like folders;
- a smart feed above or below another.

The order is **manual**: a feed added, imported or moved arrives first in its folder, feeds of a
deleted folder take its place, and the order is the same on all devices and in the OPML export.
Reordering requires being online.

## Keyboard shortcuts

| Key | Action | Key | Action |
|---|---|---|---|
| `h` | dashboard | | |
| `j` / `k` or ↓ / ↑ | next / previous article | `f` | full article: fetch, or toggle feed ↔ full |
| `m` | read / unread | `r` | summarize |
| `s` | star | `u` | unread filter |
| `v` | open original | `/` | search |
| `Esc` | back to the list | `?` | shortcuts help |

## Reading typography

*Paramètres → Lecture* offers six bundled font pairings
(identical offline and on Android): Éditorial (Source Serif 4 + Inter, default), Littéraire
(Literata), Journal (Lora + Montserrat), Moderne (Inter), Lisible (Atkinson Hyperlegible) and
Système; plus four text sizes and three column widths. Stored per device.

## Summaries

✨ summarizes the full article if available, otherwise fetches it first, otherwise falls back to the
feed content. For a YouTube video it fetches subtitles (French, then English, then the first
available) and produces chapters; clicking a timestamp seeks the video. A summary is stored:
reopening it costs nothing, ↻ regenerates it. Without subtitles, the app says so and does not try
to transcribe audio.

✨ in the reader opens a menu first, as in Inoreader, so the type is chosen before anything is
generated; the `r` shortcut generates the default type directly. The title of the summary panel
opens the same menu:

- **a question field**: ask anything about the article or video, the answer relies only on it;
- **the summary type**: standard (gist, key points, takeaway), one sentence, bullet points,
  detailed (section by section, for long content), facts vs. opinions, simplified. Each type is
  stored separately: a type marked « déjà prêt » shows instantly, and the other summaries of the
  article stay one click away under the summary;
- **Approfondi** (in-depth): the next summary uses a more capable model at maximum effort, slower
  and more expensive — useful when a long article gets a shallow summary;
- **Personnaliser** leads to *Paramètres → Résumés*: default type (also used by automatic
  summaries), personal instructions added to every summary (« I'm a developer, keep the figures »),
  and the in-depth model (empty: the usual model at maximum effort).

## Search

Online, PostgreSQL does the search (full text over title, author and content, `websearch` syntax:
`"exact phrase" -word`). Offline, it is limited to titles and excerpts stored on the device.

## Offline mode

Every read in the interface goes to a local database (IndexedDB). The last N days of articles
(30 by default, *Paramètres → Hors-ligne*) and all starred articles, with their full content, are
kept on each device. Read and star changes made offline are queued and replayed when the network
returns; on conflict the most recent change wins. Adding a feed, fetching a full article or
summarizing needs the network.

## Moving to another instance

*Paramètres → Sauvegarde et migration → Exporter toute la base* downloads a single file
(`fluxus-export-….jsonl.gz`, a few MB) with folders, feeds and their settings, articles with read
and star state, summaries and transcripts, smart feeds, layouts, dashboard and settings.

On the new instance, create the password, then *Importer une sauvegarde…*. The dialog summarizes
the file and asks for the current password and the word « REMPLACER ». The import **replaces
everything**, all or nothing: an invalid file changes nothing. Each connected device then reloads
its data.

Never exported: the password and connected devices, API keys (re-enter them: they are encrypted
with the source instance's secret), the job queue and the LLM usage log. The same file is a backup:
importing it on the original instance restores it.
