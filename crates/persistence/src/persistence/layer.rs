use std::sync::Arc;

use rusqlite::Connection;
use thiserror::Error;

use super::config::PersistenceConfig;
use super::context::AssembledContext;
use super::ids::{SessionId, TurnId};
use super::search::SearchResult;
use super::summaries::Summary;
use super::turns::Turn;

#[derive(Debug)]
pub struct PersistenceLayer {
    config: PersistenceConfig,
    connection: Arc<Connection>,
}

impl PersistenceLayer {
    pub fn new(config: PersistenceConfig) -> Result<Self, PersistenceError> {
        let connection = Connection::open_in_memory().map_err(|err| PersistenceError::Database(err.to_string()))?;
        Ok(Self {
            config,
            connection: Arc::new(connection),
        })
    }

    pub fn config(&self) -> &PersistenceConfig {
        &self.config
    }

    pub fn assemble_context(&self, _session_id: &SessionId) -> Result<AssembledContext, PersistenceError> {
        Ok(AssembledContext {
            summaries: Vec::new(),
            recent_turns: Vec::new(),
            total_turn_count: 0,
        })
    }

    pub fn load_turn(&self, _turn_id: &TurnId) -> Result<Option<Turn>, PersistenceError> {
        Ok(None)
    }

    pub fn load_summaries(&self, _session_id: &SessionId) -> Result<Vec<Summary>, PersistenceError> {
        Ok(Vec::new())
    }

    pub fn search(&self, _query: &str) -> Result<Vec<SearchResult>, PersistenceError> {
        Ok(Vec::new())
    }
}

#[derive(Debug, Error)]
pub enum PersistenceError {
    #[error("database: {0}")]
    Database(String),
    #[error("not found")]
    NotFound,
    #[error("unsupported operation: {0}")]
    Unsupported(&'static str),
}
