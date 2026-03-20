use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use super::content::ContentBlock;
use super::ids::{MessageId, SessionId, TurnId};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum Role {
    User,
    Assistant,
    System,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct TurnMetadata {
    pub model: Option<String>,
    pub input_tokens: Option<u32>,
    pub output_tokens: Option<u32>,
    pub latency_ms: Option<u64>,
    pub stop_reason: Option<String>,
    pub cost_usd: Option<f64>,
    pub triggered_by: Option<String>,
}

#[derive(Debug, Clone)]
pub struct Turn {
    pub turn_id: TurnId,
    pub session_id: SessionId,
    pub turn_index: u32,
    pub role: Role,
    pub content: Vec<ContentBlock>,
    pub content_version: u32,
    pub metadata: TurnMetadata,
    pub triggering_message_id: Option<MessageId>,
    pub is_complete: bool,
    pub created_at: DateTime<Utc>,
}
