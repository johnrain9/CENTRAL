use chrono::{DateTime, Utc};

use super::ids::SessionId;

#[derive(Debug, Clone)]
pub struct AuditEvent {
    pub event_id: String,
    pub event_type: String,
    pub session_id: Option<SessionId>,
    pub chain_id: Option<String>,
    pub details: serde_json::Value,
    pub created_at: DateTime<Utc>,
}
