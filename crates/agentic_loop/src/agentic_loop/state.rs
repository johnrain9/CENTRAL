use indexmap::IndexMap;
use serde_json::Value;

use persistence::SessionId;

use super::types::{PendingTool, PriorityClass};

pub struct AgenticLoopState {
    pub session_id: SessionId,
    pub state: LoopState,
    pub pending_tools: IndexMap<String, PendingTool>,
    pub emergency_attempted: bool,
    pub priority_class: PriorityClass,
    pub chain_id: Option<String>,
}

impl AgenticLoopState {
    pub fn new(session_id: SessionId, priority_class: PriorityClass) -> Self {
        Self {
            session_id,
            state: LoopState::Idle,
            pending_tools: IndexMap::new(),
            emergency_attempted: false,
            priority_class,
            chain_id: None,
        }
    }

    pub fn register_tool(&mut self, tool_use_id: String, tool_name: String, input: Value) {
        self.pending_tools.insert(
            tool_use_id.clone(),
            PendingTool {
                tool_use_id,
                tool_name,
                input,
                registered_at: std::time::Instant::now(),
            },
        );
        self.state = LoopState::WaitingForTool;
    }

    pub fn complete_tool(&mut self, tool_use_id: &str) {
        self.pending_tools.shift_remove(tool_use_id);
        if self.pending_tools.is_empty() {
            self.state = LoopState::Active;
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LoopState {
    Idle,
    Active,
    WaitingForTool,
}
