use super::types::{ArchivedTurnInjection, ArchivedTurnRequest, Turn};

pub fn prepare_injection(turns: Vec<Turn>, request: ArchivedTurnRequest) -> ArchivedTurnInjection {
    ArchivedTurnInjection {
        turns,
        source_request: request,
    }
}
