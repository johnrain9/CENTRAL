use chrono::{DateTime, Utc};

use super::content::ContentBlock;
use super::ids::{SessionId, SummaryId};

#[derive(Debug, Clone)]
pub struct Summary {
    pub summary_id: SummaryId,
    pub session_id: SessionId,
    pub range_start: u32,
    pub range_end: u32,
    pub depth: u32,
    pub content: Vec<ContentBlock>,
    pub content_version: u32,
    pub token_count: Option<u32>,
    pub created_at: DateTime<Utc>,
    pub superseded_by: Option<SummaryId>,
}
