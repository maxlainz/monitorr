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
SONARR_URL = "sonarr_url"
SONARR_API_KEY = "sonarr_api_key"
POLICY = "policy"
DRY_RUN = "dry_run"
WATCHED_THRESHOLD = "watched_threshold"
USER_FILTER = "plex_user_filter"
