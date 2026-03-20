use std::path::PathBuf;
use std::time::{Duration, Instant};

pub type ClaimId = String;

#[derive(Debug, Clone)]
pub struct WorkspaceClaim {
    pub claim_id: ClaimId,
    pub session_id: String,
    pub scope: ClaimScope,
    pub acquired_at: Instant,
    pub expires_at: Instant,
    pub reason: String,
}

#[derive(Debug, Clone)]
pub enum ClaimScope {
    File(PathBuf),
    Directory(PathBuf),
}

#[derive(Debug, Clone)]
pub enum ClaimResult {
    Granted(ClaimId),
    Conflict {
        holder: String,
        scope: ClaimScope,
        expires_in: Duration,
    },
}

#[derive(Debug, Clone)]
pub enum ConflictPolicy {
    Block,
    Queue { max_wait: Duration },
}

#[derive(Debug, Default)]
pub struct ClaimManager;

impl ClaimManager {
    pub fn acquire(
        &self,
        session_id: String,
        scope: ClaimScope,
        reason: String,
    ) -> ClaimResult {
        let claim_id = format!("claim_{}_{}", session_id, reason);
        let _ = WorkspaceClaim {
            claim_id: claim_id.clone(),
            session_id,
            scope,
            acquired_at: Instant::now(),
            expires_at: Instant::now() + Duration::from_secs(300),
            reason,
        };
        ClaimResult::Granted(claim_id)
    }
}
