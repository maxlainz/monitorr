"""Keys of the `setting` table and shared constants."""

PLEX_PRODUCT = "monitorr"

# Progress from which a session that disappears between polls is considered watched
# (see .claude/behavior.md → "Trigger": "the almost-complete session disappears").
NEAR_COMPLETE_PROGRESS = 0.85

# Keys in the setting table
PLEX_CLIENT_ID = "plex_client_id"
PLEX_ACCOUNT_TOKEN = "plex_account_token"
PLEX_SERVER_URI = "plex_server_uri"
PLEX_SERVER_TOKEN = "plex_server_token"
PLEX_SERVER_NAME = "plex_server_name"
# clientIdentifier of the linked PMS: the stable server identity (URIs/names can change), used to
# detect a server switch and reset the per-server sync state (history watermark, last full).
PLEX_SERVER_ID = "plex_server_id"
SONARR_URL = "sonarr_url"
SONARR_API_KEY = "sonarr_api_key"
POLICY = "policy"
DRY_RUN = "dry_run"
WATCHED_THRESHOLD = "watched_threshold"
USER_FILTER = "plex_user_filter"
WEBHOOK_SECRET = "webhook_secret"
# Incremental sync state (see sync.py): newest play seen, used as the floor for the next history
# sweep; timestamp of the last FULL reconciliation, used for the rolling full-scrape floor.
HISTORY_WATERMARK = "history_watermark"
LAST_FULL_SYNC = "last_full_sync"
