use super::types::{CompactionError, CompactionOutcome, CompactionStrategy, Session, StoredTurn, Summary};

pub struct SimpleCompactionStrategy;

impl CompactionStrategy for SimpleCompactionStrategy {
    fn compact(
        &self,
        _session: &Session,
        _summaries: Vec<Summary>,
        _turns: Vec<StoredTurn>,
    ) -> Result<CompactionOutcome, CompactionError> {
        Ok(CompactionOutcome::NotNeeded)
    }
}
