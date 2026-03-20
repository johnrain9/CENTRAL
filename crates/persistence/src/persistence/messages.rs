use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use super::ids::{MessageId, SessionId};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum MessagePattern {
    Notify,
    RequestReply,
    Broadcast,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum DeliveryStatus {
    Persisted,
    Queued,
    Delivered,
    Processed,
    ResponsePersisted,
    DeliveredUnprocessed,
    Rejected,
    Failed,
}

#[derive(Debug, Clone)]
pub struct Message {
    pub message_id: MessageId,
    pub sender_id: SessionId,
    pub channel_id: Option<String>,
    pub subject: String,
    pub body: String,
    pub pattern: MessagePattern,
    pub reply_to: Option<MessageId>,
    pub chain_id: Option<String>,
    pub chain_depth: u32,
    pub chain_cost: f64,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone)]
pub struct MessageDelivery {
    pub message_id: MessageId,
    pub recipient_session_id: SessionId,
    pub delivery_seq: u64,
    pub status: DeliveryStatus,
    pub response_message_id: Option<MessageId>,
    pub delivered_at: Option<DateTime<Utc>>,
    pub processed_at: Option<DateTime<Utc>>,
}
