"""
Error taxonomy for the TikTok extraction pipeline.

Each class corresponds 1:1 to a row in the architecture plan's §9 error
table. The UI layer should catch these by type and show the matching
user-facing message -- never collapse them into a single generic
"download failed" string. The docstring on each class IS the intended
user-facing message class; adapt wording/i18n at the UI layer, but keep the
underlying distinction.
"""


class TikTokExtractionError(Exception):
    """Base class for all extraction failures. Never raise this directly;
    raise one of the subclasses below so the failure is diagnosable."""


class PostNotFoundError(TikTokExtractionError):
    """This post no longer exists (deleted, or URL was never valid)."""


class SchemaDriftError(TikTokExtractionError):
    """TikTok changed their page structure -- extractor needs an update.

    This is deliberately a DISTINCT class from generic parse errors. If this
    fires, it should be logged at a level/category that's easy to grep for
    after a TikTok deploy (plan §12), and should trigger the extractor
    health check (plan §13) rather than being treated as one-off noise.
    """


class ContentNotAccessibleError(TikTokExtractionError):
    """Post is private, region-locked, or age-gated -- Extractor A cannot
    see it. Not necessarily a bug; may legitimately need Extractor B with a
    user-provided session cookie (plan §5, Phase 4)."""


class NoCleanGearAvailableError(TikTokExtractionError):
    """Post has video data but every available gear is watermarked. Do NOT
    catch this and silently fall back to the watermarked asset -- surface it
    to the user (plan §4, gear selection step 4)."""


class GearURLExpiredError(TikTokExtractionError):
    """The selected gear's URL is past its `expire` timestamp. Caller should
    re-run extraction once for a fresh URL before treating this as fatal."""


class VerificationMismatchError(TikTokExtractionError):
    """Post-download probe (ffprobe/image header) did not match the
    resolution/codec that was selected before download. The task must fail
    here, not report success (plan §7.4, §10)."""


class RateLimitedError(TikTokExtractionError):
    """Soft rate-limit / temporary block. Caller should back off, not retry
    in a tight loop."""


class ExtractorBUnavailableError(TikTokExtractionError):
    """Extractor B's signing dependency is broken or the feature flag is
    off. Must never propagate to or affect an Extractor-A-only task
    (plan §5 isolation requirement)."""
