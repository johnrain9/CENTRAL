use std::sync::Arc;

use inference::InferenceClient;
use persistence::{PersistenceLayer, SessionId};

use super::compaction::SimpleCompactionStrategy;
use super::config::ContextManagerConfig;
use super::types::{AssembledContext, CompactionOutcome, ContextError, TokenBudget};

pub struct ContextManager {
    persistence: Arc<PersistenceLayer>,
    inference: Arc<InferenceClient>,
    config: ContextManagerConfig,
    compaction: SimpleCompactionStrategy,
}

impl ContextManager {
    pub fn new(
        persistence: Arc<PersistenceLayer>,
        inference: Arc<InferenceClient>,
        config: ContextManagerConfig,
    ) -> Self {
        Self {
            persistence,
            inference,
            config,
            compaction: SimpleCompactionStrategy,
        }
    }

    pub fn assemble(&self, session_id: &SessionId) -> Result<AssembledContext, ContextError> {
        let _context = self.persistence.assemble_context(session_id)?;
        let mut budget = TokenBudget::new(200_000);
        budget.allocate("system_prompt", 1_000);
        Ok(AssembledContext {
            system_prompt: "[SYSTEM] Ecosystem runtime".into(),
            compression_notice: None,
            summaries: Vec::new(),
            injected_archived_turns: Vec::new(),
            recent_turns: Vec::new(),
            tool_definitions: Vec::new(),
            budget_used: budget.used(),
            budget_total: budget.total(),
        })
    }

    pub fn emergency_compact(&self, _session_id: &SessionId) -> Result<CompactionOutcome, ContextError> {
        Ok(CompactionOutcome::NotNeeded)
    }

    pub fn config(&self) -> &ContextManagerConfig {
        &self.config
    }
}
