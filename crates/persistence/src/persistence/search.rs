use super::ids::SessionId;

#[derive(Debug)]
pub struct SearchResult {
    pub source_type: String,
    pub source_id: String,
    pub session_id: SessionId,
    pub snippet: String,
    pub rank: f64,
}
