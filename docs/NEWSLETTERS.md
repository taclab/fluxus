# Newsletters by email

Fluxus can give you email addresses to subscribe to newsletters with. Each newsletter becomes a feed,
each issue an article: it shows up in the sidebar, the search, smart feeds and summaries like any
other article.

Fluxus does not run a mail server. Mail is received by **Cloudflare Email Routing** on a subdomain
of yours and handed to a small **Email Worker**, which posts it to your Fluxus instance:

```
newsletter ──SMTP──▶ Cloudflare (MX of news.example.com)
                        │  catch-all rule: *@news.example.com
                        ▼
                     Email Worker (cloudflare/email-worker/worker.js)
                        │  HTTPS POST, raw message signed with a shared secret
                        ▼
                     Fluxus  POST /api/inbound/email  ──▶ feed + article
```

## Requirements

- A domain whose DNS is managed by Cloudflare (the free plan is enough: Email Routing and Workers
  are free at this volume).
- A Fluxus instance reachable from the Internet over HTTPS (the Worker runs at Cloudflare and must be
  able to call it).
- An ordinary mailbox you own, used as a **fallback**: if Fluxus is down or answers with an error,
  the Worker forwards the message there instead of losing it.

## Setup

### 1. Choose the receiving subdomain in Fluxus

Settings → **Email newsletters** → *Receiving domain*: for example `news.example.com`.

Use a subdomain even if your domain receives no mail today: the day you want real addresses on it
(`you@example.com`), they will not clash with the newsletter routing.

### 2. Generate the Worker secret

Same section → *FLUXUS_SECRET* → **Generate the secret**. Copy it: it is shown only once. The
*FLUXUS_URL* line shows the address of your instance as the Worker must call it.

Until a secret exists, Fluxus refuses every incoming message.

### 3. Enable Email Routing on the subdomain

In the Cloudflare dashboard, open your domain → **Email** → **Email Routing**:

1. Enable Email Routing if it is not already, then in its settings add the subdomain
   (`news.example.com`). Cloudflare adds the MX and SPF records for it.
2. **Destination addresses**: add your fallback mailbox and confirm it from the email Cloudflare
   sends. Only verified addresses can receive forwarded mail.

(Dashboard labels move from time to time; the steps stay the same.)

### 4. Deploy the Email Worker

The Worker is `cloudflare/email-worker/worker.js` in this repository. Two ways to deploy it:

**From the dashboard.** Email Routing → **Email Workers** → create a Worker, replace its code with
the content of `worker.js`, deploy. Then, in the Worker's **Settings → Variables and Secrets**:

| Name | Type | Value |
|---|---|---|
| `FLUXUS_URL` | text | the *FLUXUS_URL* shown by Fluxus, e.g. `https://fluxus.example.com` |
| `FALLBACK_ADDRESS` | text | the fallback mailbox verified in step 3 |
| `FLUXUS_SECRET` | secret | the secret generated in step 2 |

**With Wrangler.**

```bash
cd cloudflare/email-worker
cp wrangler.toml.example wrangler.toml   # set FLUXUS_URL and FALLBACK_ADDRESS
npx wrangler deploy
npx wrangler secret put FLUXUS_SECRET    # paste the secret
```

### 5. Route the subdomain to the Worker

Email Routing → **Routing rules** → **Catch-all address** (for the subdomain) → action
**Send to a Worker** → choose the Worker you deployed → enable the rule.

### 6. Create an address and subscribe

Back in Fluxus (Settings → Email newsletters → *New address*), create an address and copy it. Use it
to subscribe to a newsletter. Most newsletters first send a confirmation email: it arrives in Fluxus
as an article of the new feed; open it and click the confirmation link.

## Addresses

- **Sorting address** (default): every newsletter sent to it gets its own feed, keyed by its
  `List-Id` header (or the sender address when there is none). One address can serve all your
  newsletters. Feeds are created on the first email, in the folder chosen for the address.
- **Dedicated address**: everything sent to it lands in one feed, created right away with the
  address's name. Handy for a single newsletter, or to see which sender leaks your address.
- **Revoking** an address makes Cloudflare reject the next emails sent to it; its feeds and articles
  stay. Deleting the feed of a dedicated address revokes the address too.
- Pausing a newsletter feed (feed settings) drops its new issues silently.

Addresses are random (`k7f3q9x2abcd@news.example.com`) so they cannot be guessed. They are part of
the full backup: restored on another instance, they keep working once the Worker points to it with
that instance's secret (the secret itself is never exported).

## What Fluxus does with a message

- Keeps the HTML part (or the text part, turned into paragraphs), cleans it like any article:
  scripts, styles, forms and hidden preview text are removed; tracking pixels and images embedded as
  attachments are dropped; layout tables are flattened so the issue reads top to bottom on any
  screen. Attachments are ignored.
- Takes the subject as title, the sender name as author, the `Date` header as publication date, and
  the "view online" link (when one is found) as the article link.
- Ignores a message it already has (same `Message-ID`), so a retried delivery creates no duplicate.
- Shows an **Unsubscribe** link at the bottom of the reader when the newsletter provides a
  `List-Unsubscribe` header.
- Makes no network request because of a received message. Remote images are loaded by your device
  when you read the issue, as for any article, which can tell the sender that the issue was opened.
  Links are left as they are: resolving tracking redirects would count as a click.

## Worker behaviour

| Fluxus answers | The Worker |
|---|---|
| 202 | nothing more: the newsletter is in Fluxus |
| 404 (unknown or revoked address) | rejects the message at Cloudflare |
| anything else, timeout (25 s), network error | forwards the message to `FALLBACK_ADDRESS` |
| — message over 15 MiB | forwards it to `FALLBACK_ADDRESS` without calling Fluxus |

Errors are never turned into rejections: a newsletter that receives bounces may unsubscribe you.

## Security

- `POST /api/inbound/email` has no session: it is authenticated by an HMAC-SHA256 signature of
  `<timestamp in ms>.<recipient in lowercase>.<sha256 of the body>` with the shared secret
  (`X-Fluxus-Timestamp`, `X-Fluxus-To`, `X-Fluxus-Signature`). The timestamp must be within five
  minutes; a replay within that window only hits the `Message-ID` deduplication.
- Regenerating the secret invalidates the old one at once: update the Worker right after (meanwhile
  newsletters go to the fallback mailbox).
- Message size is capped at 15 MiB before the signature is checked.

## Troubleshooting

- **Nothing arrives, the fallback mailbox gets the newsletters**: Fluxus refused the message. Check
  the Worker logs (Cloudflare → Workers → your Worker → Logs): `HTTP 401` means the secret differs
  between the Worker and Fluxus, `HTTP 503` that no secret is set in Fluxus or a backup import was
  running.
- **The sender gets a bounce "Adresse inconnue"**: the address was revoked or mistyped.
- **Nothing arrives anywhere**: check that the catch-all rule of the subdomain is enabled and points
  to the Worker, and that the MX records of the subdomain are Cloudflare's.
