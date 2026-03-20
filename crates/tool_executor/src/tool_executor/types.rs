use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;

use super::approval::ApprovalRouter;
use super::claims::{ClaimManager, ClaimResult, ClaimScope};
use super::config::ToolExecutorConfig;
use super::hooks::HookManager;
use super::path_validation::{PathValidation, PathValidator};
use super::registry::ToolRegistry;

pub struct ToolExecutor {
    pub registry: ToolRegistry,
    pub claim_manager: Arc<ClaimManager>,
    pub approval_router: Arc<ApprovalRouter>,
    pub hook_manager: HookManager,
    pub shell_limiter: ShellLimiter,
    pub path_validator: Arc<PathValidator>,
    pub truncator: Arc<OutputTruncator>,
    pub config: ToolExecutorConfig,
}

impl ToolExecutor {
    pub fn new(config: ToolExecutorConfig) -> Self {
        Self {
            registry: ToolRegistry::default(),
            claim_manager: Arc::new(ClaimManager::default()),
            approval_router: Arc::new(ApprovalRouter::default()),
            hook_manager: HookManager::default(),
            shell_limiter: ShellLimiter::new(config.max_shell_concurrency),
            path_validator: Arc::new(PathValidator::default()),
            truncator: Arc::new(OutputTruncator::default()),
            config,
        }
    }

    pub async fn execute(&self, request: ToolCallRequest, _ctx: &ToolContext) -> ToolOutcome {
        ToolOutcome::Success {
            tool_use_id: request.tool_use_id,
            content: ToolContent::Text("tool execution stub".into()),
        }
    }

    pub fn acquire_claim(
        &self,
        session_id: String,
        scope: ClaimScope,
        reason: String,
    ) -> ClaimResult {
        self.claim_manager.acquire(session_id, scope, reason)
    }

    pub fn validate_path(&self, path: &PathBuf) -> PathValidation {
        self.path_validator.validate(path)
    }
}

#[derive(Debug, Clone)]
pub struct ToolCallRequest {
    pub tool_use_id: String,
    pub tool_name: String,
    pub input: serde_json::Value,
}

#[derive(Debug, Clone)]
pub struct ToolContext {
    pub session_id: String,
    pub cwd: PathBuf,
    pub workspace_roots: Vec<PathBuf>,
    pub trusted: bool,
}

#[derive(Debug, Clone)]
pub enum ToolOutcome {
    Success { tool_use_id: String, content: ToolContent },
    Error { tool_use_id: String, message: String },
    Blocked { tool_use_id: String, hook_name: String, reason: String },
    Denied { tool_use_id: String, reason: String },
    ClaimConflict {
        tool_use_id: String,
        holder_session_id: String,
        holder_session_name: String,
        scope: ClaimScope,
        expires_in: Duration,
    },
}

#[derive(Debug, Clone)]
pub enum ToolContent {
    Text(String),
    Json(serde_json::Value),
    Image { format: String, data: Vec<u8> },
    Multi(Vec<ToolContent>),
}

pub struct ShellLimiter {
    concurrency: usize,
}

impl ShellLimiter {
    pub fn new(concurrency: usize) -> Self {
        Self { concurrency }
    }
}

#[derive(Debug, Default)]
pub struct OutputTruncator {
    pub max_bytes: usize,
}

pub trait ToolOutputReducer: Send + Sync {
    fn reduce(&self, tool_name: &str, content: &ToolContent) -> ToolContent;
}

#[async_trait]
pub trait NativeTool: Send + Sync {
    async fn execute(&self, input: serde_json::Value, ctx: &ToolContext) -> Result<ToolContent, String>;
    fn name(&self) -> &str;
    fn timeout_ms(&self) -> Option<u64> {
        None
    }
    fn is_mutating(&self) -> bool {
        false
    }
}

#[async_trait]
pub trait ExtensionTool: Send + Sync {
    async fn execute(&self, input: serde_json::Value, ctx: &ToolContext) -> Result<ToolContent, String>;
    fn name(&self) -> &str;
    fn input_schema(&self) -> serde_json::Value;
    fn description(&self) -> &str;
    fn is_mutating(&self) -> bool;
    fn timeout_ms(&self) -> Option<u64> {
        None
    }
}
