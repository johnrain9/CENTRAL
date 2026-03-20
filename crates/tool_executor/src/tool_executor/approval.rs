use std::time::{Duration, Instant};

pub type ApprovalRequestId = String;

#[derive(Debug, Clone)]
pub struct ApprovalRequest {
    pub request_id: ApprovalRequestId,
    pub session_id: String,
    pub session_name: String,
    pub tool_name: String,
    pub tool_input: serde_json::Value,
    pub created_at: Instant,
    pub timeout: Duration,
}

#[derive(Debug, Clone)]
pub enum ApprovalDecision {
    Approve,
    Deny { reason: Option<String> },
    Timeout,
}

#[derive(Debug, Default)]
pub struct ApprovalRouter;

impl ApprovalRouter {
    pub fn submit(&self, _request: ApprovalRequest) {}
}
