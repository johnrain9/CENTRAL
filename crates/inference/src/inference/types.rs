use serde::{Deserialize, Serialize};

use super::config::{InferenceConfig, ToolConfig};
use super::priority::Priority;

pub type SessionId = String;
pub type RequestId = String;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SystemBlock {
    pub text: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum MessageRole {
    User,
    Assistant,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Message {
    pub role: MessageRole,
    pub content: Vec<ContentBlock>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum ContentBlock {
    #[serde(rename = "text")]
    Text { text: String },
    #[serde(rename = "tool_use")]
    ToolUse {
        tool_use_id: String,
        name: String,
        input: serde_json::Value,
    },
    #[serde(rename = "tool_result")]
    ToolResult {
        tool_use_id: String,
        content: String,
        is_error: bool,
    },
    #[serde(rename = "image")]
    Image { format: String, data: Vec<u8> },
    #[serde(rename = "thinking")]
    Thinking { text: String },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InferenceRequest {
    pub session_id: SessionId,
    pub model_id: String,
    pub system: Vec<SystemBlock>,
    pub messages: Vec<Message>,
    pub tool_config: Option<ToolConfig>,
    pub inference_config: InferenceConfig,
    pub priority: Priority,
    pub estimated_input_tokens: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct TokenUsage {
    pub input_tokens: u32,
    pub output_tokens: u32,
    pub cache_read_input_tokens: Option<u32>,
    pub cache_creation_input_tokens: Option<u32>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StopReason {
    EndTurn,
    ToolUse,
    MaxTokens,
    StopSequence,
    ContentFiltered,
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum InferenceError {
    #[error("throttled")]
    Throttled,
    #[error("server error")]
    ServerError,
    #[error("network error")]
    NetworkError,
    #[error("context overflow")]
    ContextOverflow,
    #[error("validation error")]
    Validation,
    #[error("auth error")]
    AuthError,
    #[error("model not found")]
    ModelNotFound,
    #[error("content filtered")]
    ContentFiltered,
    #[error("cancelled")]
    Cancelled,
    #[error("unknown error")]
    Unknown,
}

impl InferenceError {
    pub fn is_retriable(&self) -> bool {
        matches!(self, Self::Throttled | Self::ServerError | Self::NetworkError)
    }

    pub fn needs_compaction(&self) -> bool {
        matches!(self, Self::ContextOverflow)
    }
}
