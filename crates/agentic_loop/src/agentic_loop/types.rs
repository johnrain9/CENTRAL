use std::sync::Arc;
use std::time::{Duration, Instant};

use chrono::{DateTime, Utc};
use serde_json::Value;
use thiserror::Error;

use context_manager::ContextManager;
use inference::{InferenceClient, InferenceError};
use persistence::{PersistenceError, PersistenceLayer, SessionId, TurnId};
use tool_executor::{ToolContent, ToolExecutor};

pub type ChainId = String;

#[derive(Debug)]
pub struct LoopDependencies {
    pub inference_client: Arc<InferenceClient>,
    pub tool_executor: Arc<ToolExecutor>,
    pub context_manager: Arc<ContextManager>,
    pub persistence: Arc<PersistenceLayer>,
    pub message_router: Arc<dyn MessageRouter>,
    pub session_manager: Arc<dyn SessionManager>,
    pub human_interface: Arc<dyn HumanInterface>,
    pub config: LoopConfig,
}

#[derive(Debug, Clone)]
pub struct LoopConfig {
    pub idle_timeout: Option<Duration>,
}

impl Default for LoopConfig {
    fn default() -> Self {
        Self {
            idle_timeout: Some(Duration::from_secs(300)),
        }
    }
}

#[derive(Debug)]
pub struct TurnRecord {
    pub turn_id: TurnId,
    pub session_id: SessionId,
    pub chain_id: Option<ChainId>,
    pub input: TurnInput,
    pub assistant_response: AssistantResponse,
    pub tool_results: Vec<ToolResult>,
    pub cost: InferenceCost,
    pub duration: Duration,
    pub timestamp: DateTime<Utc>,
}

#[derive(Debug, Default)]
pub struct TurnInput {
    pub content: Vec<inference::ContentBlock>,
}

#[derive(Debug, Default)]
pub struct AssistantResponse {
    pub content: Vec<inference::ContentBlock>,
}

#[derive(Debug, Clone)]
pub struct ToolResult {
    pub tool_use_id: String,
    pub outcome: ToolContent,
}

#[derive(Debug, Default, Clone, Copy)]
pub struct InferenceCost {
    pub input_tokens: u32,
    pub output_tokens: u32,
    pub cost_microdollars: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum PriorityClass {
    HumanInteractive = 1,
    ChainReply = 2,
    Worker = 3,
    Autonomous = 4,
    Background = 5,
}

#[derive(Debug, Error)]
pub enum LoopError {
    #[error("inference: {0}")]
    Inference(#[from] InferenceError),
    #[error("persistence: {0}")]
    Persistence(#[from] PersistenceError),
    #[error("context assembly failed: {0}")]
    ContextAssembly(String),
    #[error("session poisoned: {reason}")]
    Poisoned { reason: String },
    #[error("context exhausted after emergency compaction")]
    ContextExhausted,
}

#[derive(Debug, Clone)]
pub enum TurnOutcome {
    Complete,
    WaitingForTool,
    WaitingForHuman,
}

#[derive(Debug, Clone)]
pub struct PendingTool {
    pub tool_use_id: String,
    pub tool_name: String,
    pub input: Value,
    pub registered_at: Instant,
}

pub trait MessageRouter: Send + Sync {
    fn reserve_budget(&self, _session_id: &SessionId, _tokens: u32) {}
}

pub trait SessionManager: Send + Sync {}

pub trait HumanInterface: Send + Sync {}
