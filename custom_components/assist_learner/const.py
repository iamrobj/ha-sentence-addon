"""Constants for Assist Learner."""

from __future__ import annotations

DOMAIN = "assist_learner"
NAME = "Assist Learner"

# Config / options keys
CONF_TARGET_AGENT = "target_agent"
CONF_LANGUAGE = "language"
CONF_RUN_TIME = "run_time"
CONF_MIN_OCCURRENCES = "min_occurrences"
CONF_LOOKBACK_DAYS = "lookback_days"
CONF_AUTO_APPROVE = "auto_approve"
CONF_AUTO_APPROVE_THRESHOLD = "auto_approve_threshold"
CONF_ENABLED_LEARNERS = "enabled_learners"
CONF_RETENTION_DAYS = "retention_days"
CONF_MAX_PROPOSALS = "max_proposals_per_night"
CONF_NOTIFY = "notify"
CONF_AI_TASK = "ai_task_entity"
CONF_SEND_STATES = "send_states_to_llm"
CONF_MAX_SENTENCES = "max_sentences_per_command"
CONF_ALLOWED_SERVICES = "allowed_services"
CONF_RATE_LIMIT = "rate_limit_per_minute"
CONF_EXEC_LOG_SIZE = "execution_log_size"
CONF_CARRIER_VERBS = "carrier_verbs"
CONF_DEFAULT_PLAYER = "default_player"
CONF_AREA_PLAYERS = "preferred_player_per_area"
CONF_PREFER_OWN_PLAYER = "prefer_calling_device_player"

DEFAULT_OPTIONS: dict = {
    CONF_RUN_TIME: "03:15:00",
    CONF_MIN_OCCURRENCES: 1,
    CONF_LOOKBACK_DAYS: 14,
    CONF_AUTO_APPROVE: False,
    CONF_AUTO_APPROVE_THRESHOLD: 5,
    CONF_ENABLED_LEARNERS: ["music_play"],
    CONF_RETENTION_DAYS: 0,
    CONF_MAX_PROPOSALS: 10,
    CONF_NOTIFY: "persistent",
    CONF_AI_TASK: None,
    CONF_SEND_STATES: True,
    CONF_MAX_SENTENCES: 12,
    CONF_RATE_LIMIT: 6,
    CONF_EXEC_LOG_SIZE: 500,
    CONF_CARRIER_VERBS: ["play", "put on", "stick on", "queue", "start"],
    CONF_DEFAULT_PLAYER: None,
    CONF_AREA_PLAYERS: {},
    CONF_PREFER_OWN_PLAYER: True,
}

NOTIFY_NONE = "none"
NOTIFY_PERSISTENT = "persistent"

EVENT_PROPOSALS_CREATED = f"{DOMAIN}_proposals_created"

# Proposal / miss statuses
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_SUPERSEDED = "superseded"
