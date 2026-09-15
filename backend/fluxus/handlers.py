"""Importe les modules qui enregistrent des handlers de jobs."""

from .extract import jobs as _extract_jobs  # noqa: F401
from .fetcher import jobs as _fetcher_jobs  # noqa: F401
from .smartfeeds import jobs as _smartfeed_jobs  # noqa: F401
