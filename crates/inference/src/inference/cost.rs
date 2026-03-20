use chrono::{DateTime, Utc};

use super::types::{RequestId, TokenUsage};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Cost {
    pub cost_microdollars: u64,
}

impl Cost {
    pub fn new(cost_microdollars: u64) -> Self {
        Self { cost_microdollars }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BudgetScope {
    Session(String),
    Global,
}

#[derive(Debug, Clone)]
pub struct CostEvent {
    pub session_id: String,
    pub request_id: RequestId,
    pub model_id: String,
    pub usage: TokenUsage,
    pub cost: Cost,
    pub timestamp: DateTime<Utc>,
    pub scope: BudgetScope,
}
