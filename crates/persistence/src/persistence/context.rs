use super::summaries::Summary;
use super::turns::Turn;

#[derive(Debug)]
pub struct AssembledContext {
    pub summaries: Vec<Summary>,
    pub recent_turns: Vec<Turn>,
    pub total_turn_count: u32,
}
