use std::collections::HashMap;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use inference::types::InferenceError;
use persistence::{PersistenceError, SessionId, SummaryId, Turn};

pub type TurnIndex = u32;

#[derive(Debug, Clone)]
pub struct Summary {
    pub id: SummaryId,
    pub session_id: SessionId,
    pub range_start: TurnIndex,
    pub range_end: TurnIndex,
    pub content: String,
    pub compression_depth: u32,
    pub status: SummaryStatus,
    pub idempotency_key: IdempotencyKey,
    pub generated_at: DateTime<Utc>,
    pub model_id: String,
    pub input_tokens: usize,
    pub output_tokens: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SummaryStatus {
    Active,
    Superseded,
}

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct IdempotencyKey(pub String);

#[derive(Debug, Clone)]
pub struct AssembledContext {
    pub system_prompt: String,
    pub compression_notice: Option<String>,
    pub summaries: Vec<Summary>,
    pub injected_archived_turns: Vec<Turn>,
    pub recent_turns: Vec<Turn>,
    pub tool_definitions: Vec<ToolDefinition>,
    pub budget_used: usize,
    pub budget_total: usize,
}

#[derive(Debug, Clone)]
pub struct ToolDefinition {
    pub name: String,
    pub description: String,
}

#[derive(Debug, Clone)]
pub struct ArchivedTurnInjection {
    pub turns: Vec<Turn>,
    pub source_request: ArchivedTurnRequest,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ArchivedTurnRequest {
    pub range_start: TurnIndex,
    pub range_end: TurnIndex,
}

#[derive(Debug, Clone)]
pub struct TurnMetadata {
    pub compaction_summary: Option<String>,
    pub summarized: bool,
    pub covered_by_summary: Option<SummaryId>,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone)]
pub enum CompactionOp {
    InsertSummary(Summary),
    SupersedeSummaries(Vec<SummaryId>),
    MarkTurnsSummarized { range_start: TurnIndex, range_end: TurnIndex },
}

#[derive(Debug, Clone)]
pub struct CompactionTransaction {
    pub idempotency_key: IdempotencyKey,
    pub operations: Vec<CompactionOp>,
}

#[derive(Debug, Clone)]
pub struct TokenBudget {
    total: usize,
    allocated: HashMap<String, usize>,
}

impl TokenBudget {
    pub fn new(total: usize) -> Self {
        Self {
            total,
            allocated: HashMap::new(),
        }
    }

    pub fn reserve(&mut self, label: &str, tokens: usize) -> Result<(), ContextError> {
        if self.used() + tokens > self.total {
            return Err(ContextError::BudgetExceeded {
                label: label.to_string(),
                requested: tokens,
                remaining: self.remaining(),
            });
        }
        *self.allocated.entry(label.to_string()).or_default() += tokens;
        Ok(())
    }

    pub fn try_allocate(&mut self, label: &str, tokens: usize) -> bool {
        if self.used() + tokens > self.total {
            return false;
        }
        *self.allocated.entry(label.to_string()).or_default() += tokens;
        true
    }

    pub fn allocate(&mut self, label: &str, tokens: usize) {
        *self.allocated.entry(label.to_string()).or_default() += tokens;
    }

    pub fn used(&self) -> usize {
        self.allocated.values().sum()
    }

    pub fn remaining(&self) -> usize {
        self.total.saturating_sub(self.used())
    }

    pub fn total(&self) -> usize {
        self.total
    }
}

#[derive(Debug, Clone)]
pub enum CompactionOutcome {
    Compacted {
        summary: Summary,
        turns_compressed: usize,
        tokens_before: usize,
        tokens_after: usize,
    },
    NotNeeded,
    Fallback { reason: String },
}

#[derive(Debug, thiserror::Error)]
pub enum ContextError {
    #[error("token budget exceeded for {label}")]
    BudgetExceeded {
        label: String,
        requested: usize,
        remaining: usize,
    },
    #[error("summary generation failed: {0}")]
    SummaryGenerationFailed(#[from] InferenceError),
    #[error("persistence error: {0}")]
    PersistenceError(#[from] PersistenceError),
    #[error("context exhausted")]
    ContextExhausted,
    #[error("invalid config: {0}")]
    InvalidConfig(String),
}

#[derive(Debug, thiserror::Error)]
pub enum CompactionError {
    #[error("summary generation failed: {0}")]
    SummaryGenerationFailed(#[from] InferenceError),
    #[error("budget exhausted (needed {estimated_cost}, remaining {remaining_budget})")]
    BudgetExhausted {
        estimated_cost: usize,
        remaining_budget: usize,
    },
    #[error("persistence error: {0}")]
    PersistenceError(#[from] PersistenceError),
}

#[derive(Debug, thiserror::Error)]
pub enum ConfigError {
    #[error("fixed overhead {fixed_overhead} exceeds window {model_context_window}")]
    FixedOverheadExceedsWindow {
        fixed_overhead: usize,
        min_recent_cost: usize,
        model_context_window: usize,
        suggestion: String,
    },
}

pub trait CompactionThreshold: Send + Sync {
    fn should_compact(&self, metrics: &ContextMetrics) -> bool;
}

pub trait CompactionStrategy: Send + Sync {
    fn compact(
        &self,
        session: &Session,
        summaries: Vec<Summary>,
        turns: Vec<StoredTurn>,
    ) -> Result<CompactionOutcome, CompactionError>;
}

#[derive(Debug, Clone)]
pub struct ContextMetrics {
    pub estimated_tokens: usize,
    pub context_window: usize,
    pub summary_count: usize,
    pub raw_turn_count: usize,
    pub compression_depth: u32,
    pub accuracy_ratio: f64,
}

impl ContextMetrics {
    pub fn adjusted_token_estimate(&self) -> usize {
        if self.accuracy_ratio > 1.0 {
            (self.estimated_tokens as f64 * self.accuracy_ratio).ceil() as usize
        } else {
            self.estimated_tokens
        }
    }
}

#[derive(Debug, Clone)]
pub struct StoredTurn {
    pub turn: Turn,
    pub metadata: TurnMetadata,
}

#[derive(Debug, Clone)]
pub struct Session {
    pub session_id: SessionId,
}
