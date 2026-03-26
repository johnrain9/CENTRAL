use persistence::Turn;
use super::types::{ArchivedTurnInjection, ArchivedTurnRequest};

pub fn prepare_injection(turns: Vec<Turn>, request: ArchivedTurnRequest) -> ArchivedTurnInjection {
    ArchivedTurnInjection {
        turns,
        source_request: request,
    }
}
